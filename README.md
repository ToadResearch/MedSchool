# MedSchool 🩺 🏥

> Imagine if all major coding benchmarks were multiple-choice QA

That's the current state of affairs for medical/clinical benchmarking, outside of maybe the new conversational HealthBench that has the big lab spotlight on it right now.

Medical/clinical data is semi-verifiable: it's a mix of objective and subjective statements, free-text and discrete fields, etc. EHRs store all this data and serve as ground truth repositories for documented clinical realities, and because they are highly structured, they are implicitly verifiable. But to the best of our knowledge, there have been no public works applying RLVR to agentic EHR tasks. We aim to change that!

We think that EHRs are the gateway to most clinical tasks. From what we've seen with programming, we believe the best way to develop clinical intelligence is by giving models the ability to take action and learn from experience within EHR environments. Some more about *the plan* can be found [here](https://x.com/mkieffer1107/status/1958644405411225788). We might spruce this up in the future and turn it into a blog post.

---
### Want to help?

![Under construction](assets/under_construction.gif)

> This project is actively under development and there are many known bugs!


Right now we only have basic MCP support, and are beginning to work on the environment itself. The two biggest challenges to solve:

1) Figure out the minimal MCP toolset to best handle EHR tasks. Many to choose from [here](https://zitniklab.hms.harvard.edu/TxAgent/) and [here](https://github.com/snap-stanford/Biomni).
2) Figure out how to generate enviroment tasks automatically. It should be *relatively* easy to generate single-hop tasks.

If you're interested in clinical intelligence, developing realistic health/medical benchmarks, or creating an open-source copilot for doctors, consider helping out!



---

### How to run:

A basic MCP server demo is available inside [mcp_demo/](mcp_demo) with instructions. 

The full environment is available inside [environment/](environment).

---

### How to remove everything:

To stop all services and completely delete all containers, data volumes, and associated images, run the purge command:
```sh
./shutdown.sh --purge
```


