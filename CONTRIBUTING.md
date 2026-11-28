# Contributing

Thanks for helping. Tenkyu is small on purpose: standard library only, no network calls, explainable decisions.

## Set up and test

```
pip install -e .[dev]
python -m pytest -q
```

## Ground rules

- Keep it dependency-free and offline. A new signal must not make network calls.
- Every signal returns a reason a support person can read.
- Add a test for every bug you fix. Junk input must never make `check` raise.
- The README is tested: python blocks run, and the demo and benchmark tables must equal real output. If you change behavior, run the tests and update the README.
- Benchmark numbers come from `benchmarks/farming_bench.py`. Do not edit them by hand, and keep the caveat that the data is synthetic.
- Do not bundle third-party disposable-domain lists unless their license allows it. Point to them and load them with `load_domains`.
- Be honest about limits in the README. Say what you tested and what you did not.
