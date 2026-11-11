"""The five agents and the runner that executes them.

    Reader     agents/reader.py     citizen message -> facts + intent           (realtime)
    Replier    agents/replier.py    free question -> answer from engine JSON     (realtime)
    Scout      agents/scout.py      profile group -> needs + scheme names        (gap)
    Extractor  agents/extractor.py  official page -> rules with source quotes    (gap, batch)
    Verifier   agents/verifier.py   flagged rule + its quote -> supported?       (gap, batch)

Each agent has one job, a JSON contract and a code check. Agents never call one another and never
decide eligibility: saathi/orchestrator.py and worker/ move work between them, and core/ decides.
Run any agent with agents.runner.run_agent(SPEC, input); it never raises.
"""
