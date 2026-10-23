"""Own only processes launched by this bridge; no process-name discovery."""
import ctypes
import os
import signal
import subprocess
from ctypes import wintypes

class ProcessOwner:
    def __init__(self):
        self.process = None
        self.job = None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            k = self.kernel
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            class Basic(ctypes.Structure):
                _fields_ = [("process_time",ctypes.c_int64),("job_time",ctypes.c_int64),
                    ("flags",wintypes.DWORD),("min_ws",ctypes.c_size_t),("max_ws",ctypes.c_size_t),
                    ("active",wintypes.DWORD),("affinity",ctypes.c_size_t),
                    ("priority",wintypes.DWORD),("scheduling",wintypes.DWORD)]
            class IO(ctypes.Structure):
                _fields_ = [(name,ctypes.c_uint64) for name in ("read_ops","write_ops","other_ops","read_bytes","write_bytes","other_bytes")]
            class Extended(ctypes.Structure):
                _fields_ = [("basic",Basic),("io",IO),("process_memory",ctypes.c_size_t),
                    ("job_memory",ctypes.c_size_t),("peak_process",ctypes.c_size_t),
                    ("peak_job",ctypes.c_size_t)]
            self.job = k.CreateJobObjectW(None,None)
            if not self.job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits=Extended()
            limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(self.job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                self.close()
                raise RuntimeError("job_setup_failed")

    def spawn(self,args,**kwargs):
        if self.process is not None:
            raise RuntimeError("owner_already_used")
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags",0) | 0x4  # CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = True
        try:
            self.process=subprocess.Popen(args,**kwargs)
        except Exception:
            self.close()
            raise
        if os.name == "nt":
            try:
                if not self.kernel.AssignProcessToJobObject(self.job,int(self.process._handle)):
                    raise RuntimeError("job_assignment_failed")
                self._resume_owned_threads()
            except Exception:
                self.process.kill()
                self.process.wait(timeout=2)
                self.close()
                raise
        return self.process

    def _resume_owned_threads(self):
        class Entry(ctypes.Structure):
            _fields_=[("size",wintypes.DWORD),("usage",wintypes.DWORD),
                ("tid",wintypes.DWORD),("pid",wintypes.DWORD),
                ("base",wintypes.LONG),("delta",wintypes.LONG),("flags",wintypes.DWORD)]
        k=self.kernel
        k.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        k.Thread32First.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
        k.Thread32Next.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
        k.OpenThread.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        k.OpenThread.restype=wintypes.HANDLE
        k.ResumeThread.argtypes=[wintypes.HANDLE]
        k.ResumeThread.restype=wintypes.DWORD
        snapshot=k.CreateToolhelp32Snapshot(4,0)
        if snapshot==ctypes.c_void_p(-1).value:
            raise RuntimeError("thread_snapshot_failed")
        resumed=False
        try:
            entry=Entry();entry.size=ctypes.sizeof(entry)
            present=k.Thread32First(snapshot,ctypes.byref(entry))
            while present:
                if entry.pid==self.process.pid:
                    handle=k.OpenThread(2,False,entry.tid)
                    if not handle:
                        raise RuntimeError("owned_thread_open_failed")
                    try:
                        if k.ResumeThread(handle)==0xffffffff:
                            raise RuntimeError("owned_thread_resume_failed")
                        resumed=True
                    finally:k.CloseHandle(handle)
                present=k.Thread32Next(snapshot,ctypes.byref(entry))
            if not resumed:
                raise RuntimeError("owned_thread_missing")
        finally:k.CloseHandle(snapshot)

    def close(self):
        if os.name == "nt":
            if self.job:
                self.kernel.CloseHandle(self.job)
                self.job=None
        elif self.process:
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
