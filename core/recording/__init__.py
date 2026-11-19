"""
Continuous experiment recording: platform side.

hook     -- run start/end notifications from the harness (no-op when no
            recording is open)
session  -- builds the platform readers and the writer, loads the recorder
            registered under the entry point group alems.recorders, and
            opens/closes a recording around an experiment
"""
