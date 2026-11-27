"""Fixed local transport for the existing Seerr movie controller."""

from pathlib import Path
import socket
from uuid import UUID

from .approved_effect import EffectDenied, EffectUncertain
from .assistant_media_plan import MediaPlanUnavailable, sanitize_media_plan
from .assistant_media_selector import MAX_TMDB_ID
from .host_effect_socket import MAX_REQUEST, MAX_RESPONSE, _encode, _read_line

PLAN = "media.plan_movie"
REQUEST = "media.request_movie"
TIMEOUT = 95  # Host CLI deadline is shorter than Control's 120-second deadline.


def movie_target(value):
    if type(value) is not dict or set(value) != {"tmdb_id"}:
        raise EffectDenied("effect_denied")
    target = value["tmdb_id"]
    if type(target) is not int or not 1 <= target <= MAX_TMDB_ID:
        raise EffectDenied("effect_denied")
    return {"tmdb_id": target}


def movie_request_id(value):
    try:
        key = UUID(value)
        if key.version == 4 and str(key) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise EffectDenied("effect_denied")


def checked_receipt(value, tmdb_id):
    if type(value) is dict and value.get("mutation_attempted") is False:
        try:
            plan = sanitize_media_plan({"tmdb_id": tmdb_id}, value)
            if not plan["would_request"]:
                return value
        except MediaPlanUnavailable:
            pass
        raise EffectUncertain("effect_outcome_uncertain")
    if (type(value) is not dict
            or set(value) != {"tmdb_id", "seerr_request_id", "request_status", "effect_performed"}
            or type(value["tmdb_id"]) is not int or value["tmdb_id"] != tmdb_id
            or type(value["seerr_request_id"]) is not int
            or not 1 <= value["seerr_request_id"] <= MAX_TMDB_ID
            or type(value["request_status"]) is not int or value["request_status"] not in (1, 2, 5)
            or value["effect_performed"] is not True):
        raise EffectUncertain("effect_outcome_uncertain")
    return value


class MovieSocketClient:
    def __init__(self, path: Path):
        self.path = path

    def _exchange(self, operation, arguments, request_id=None):
        raw = _encode({"operation": operation, "arguments": arguments,
                       "request_id": request_id}, MAX_REQUEST)
        connected = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(TIMEOUT)
                peer.connect(str(self.path))
                connected = True
                peer.sendall(raw)
                response = _read_line(peer, MAX_RESPONSE)
        except (OSError, EffectDenied):
            if operation == PLAN:
                raise MediaPlanUnavailable("media_plan_unavailable") from None
            if connected:
                raise EffectUncertain("effect_outcome_uncertain") from None
            raise EffectDenied("effect_denied") from None
        if type(response) is not dict or set(response) != {"status", "result", "request_id"}:
            raise EffectUncertain("effect_outcome_uncertain")
        if response["request_id"] != request_id:
            raise EffectUncertain("effect_outcome_uncertain")
        if response["status"] == "denied" and response["result"] is None:
            raise EffectDenied("effect_denied")
        if response["status"] != "completed" or type(response["result"]) is not dict:
            raise EffectUncertain("effect_outcome_uncertain")
        return response["result"]

    def plan(self, selector):
        return self._exchange(PLAN, selector)

    def request(self, value, *, request_id):
        selector = movie_target(value)
        receipt = self._exchange(REQUEST, selector, movie_request_id(request_id))
        receipt = checked_receipt(receipt, selector["tmdb_id"])
        if receipt.get("mutation_attempted") is False:
            return {**receipt, "tmdb_id": selector["tmdb_id"], "effect_performed": False}
        return receipt
