"""PID 1 reaps orphaned children until the manager stops this sandbox."""
import os
import signal
import time


def reap(signum, frame):
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        pass


signal.signal(signal.SIGCHLD, reap)
while True:
    time.sleep(60)
