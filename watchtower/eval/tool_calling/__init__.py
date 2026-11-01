"""W4 go/no-go gate: can the local model call the Tier 0 tools reliably? (Architecture §9.4)

    cases.py       the pre-registered cases (C1-C6)
    thresholds.py  the pre-registered pass marks
    score.py       per-trial scoring (pure functions)
    run.py         runner: cases x seeds against a live model, one SQLite row per call
    report.py      metrics + verdict from the SQLite rows -> results/reports/*.md

Cases and thresholds are committed before the first live run. Changing either afterwards
means the run before the change no longer counts as the gate.
"""
