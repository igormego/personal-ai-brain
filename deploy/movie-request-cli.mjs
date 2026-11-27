// Install beside the pinned existing Homelab controller and read adapters.
// The host service is the only caller, after exact approval and durable reservation.
import { readMovieIntelligence } from './sqlite.mjs';
import { readRadarr } from './radarr.mjs';
import { createMovieRequestController } from './seerr-request.mjs';

try {
  const [value, ...rest] = process.argv.slice(2);
  const tmdb = Number(value);
  if (rest.length || !/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(tmdb)) {
    throw Error('invalid_movie');
  }
  const controller = createMovieRequestController({
    seerr: { baseUrl: process.env.SEERR_BASE_URL, apiKey: process.env.SEERR_API_KEY },
    movieIntelligence: () => readMovieIntelligence({
      dbPath: process.env.MOVIE_DB_PATH, schemaMode: 'normalized',
    }),
    radarr: () => readRadarr({
      baseUrl: process.env.RADARR_BASE_URL, apiKey: process.env.RADARR_API_KEY,
    }),
  });
  const result = await controller.request_movie({ tmdb_id: tmdb }, { execute: true });
  if (result.audit?.mutation_attempted === false && result.decision !== 'requestable' &&
      (!result.identity || result.identity.tmdb_id === tmdb)) {
    const { contract_version, decision, reason, identity, evidence } = result;
    process.stdout.write(JSON.stringify({
      contract_version, decision, reason, identity, evidence, mutation_attempted: false,
    }) + '\n');
  } else {
    const receipt = result.request;
    if (result.decision !== 'requested' || result.audit?.mutation_attempted !== true ||
        result.identity?.tmdb_id !== tmdb || receipt?.tmdb_id !== tmdb || receipt.is4k !== false ||
        !Number.isSafeInteger(receipt.id) || receipt.id < 1 || ![1, 2, 5].includes(receipt.status)) {
      throw Error('no_confirmed_request');
    }
    process.stdout.write(JSON.stringify({
      tmdb_id: tmdb, seerr_request_id: receipt.id, request_status: receipt.status, effect_performed: true,
    }) + '\n');
  }
} catch {
  process.exitCode = 2;
}
