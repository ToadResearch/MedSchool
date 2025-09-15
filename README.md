# MedSchool 🩺 🏥

> Imagine if all major coding benchmarks were multiple-choice QA

That's the current state of affairs for medical/clinical benchmarking, outside of the new conversational HealthBench that currently has the big lab spotlight on it.

Medical/clinical data is semi-verifiable: it's a mix of objective and subjective statements, free-text and discrete fields, etc. EHRs store all this data and serve as ground truth repositories for documented clinical realities, and because they are highly structured, they are implicitly verifiable. But to the best of our knowledge, there have been no public works applying RLVR to agentic EHR tasks. We aim to change that!

We think that EHRs are the gateway to most clinical tasks. From what we've seen with programming, we believe the best way to develop clinical intelligence is by giving models the ability to take action and learn from experience within EHR environments. Some more about *the plan* can be found [here](https://x.com/mkieffer1107/status/1958644405411225788). We might spruce this up in the future and turn it into a blog post.

---
### Want to help?

![Under construction](assets/under_construction.gif)

> This project is actively under development and there are many known bugs!


The two biggest challenges to solve:

1) Figure out the minimal toolset to best handle EHR tasks. Many to choose from [here](https://zitniklab.hms.harvard.edu/TxAgent/) and [here](https://github.com/snap-stanford/Biomni).
2) Figure out how to generate enviroment tasks automatically. It should be *relatively* easy to generate single-hop tasks.

If you're interested in clinical intelligence, developing realistic health/medical benchmarks, or creating an open-source copilot for doctors, consider helping out!



---

### How to run:

A basic MCP server demo is available inside [mcp_demo/](mcp_demo) with instructions. 

The full environment is available inside [environment/](environment). It's a work in progress, and we'll eventually put full instructions on how to run it from here.

---

### Roadmap


1) **Figure out good multi-step tasks**:
  We want to create tasks that go beyond single-hop retrieval problems. In the short-term, while using synthetic data, we think CRUD tasks should work pretty well. For example, a task might be specified like 
    ```json
        "id": "1",
        "type": ["read"],
        "input": {
          "task": "How many patients are in the FHIR database? Do not use commas in the numbers.",
          "context": ""
        },
        "output": {
          "answer": 141
        }
    ```
    where the `type` field allows for multiple CRUD types, depending on the complexity of the task (this would also allow for resource locking and/or task management to ensure parallel agents don't modify the same resource; we'll likely also expand the format to include resource IDs in the metadata to assist with this). Right now everything is hard-coded, but it should be fairly simple to implement a FHIR verifier that can programatically find correct answers for new datasets, given a resource ID (our current Synthea dataset is deterministic, and any future benchmarks will be too).

    Although Synthea doesn't carry meaningful, clinical signal, it might also be interesting to try fill-in-the-middle and next-clinical-event prediction to test short-term clinical ability. This would work best with real data.

2) **Create a benchmark**:
  Once we have a good handle on tasks, we'd like to create a benchmark that tests an LLMs ability to perform realistic, multi-step tasks inside an EHR. We imagine that this would be iterative as we figure out new tasks and tools to add.

3) **Figure out better tools**
    Context management is the name of the game! FHIR resources can be extremely long and quickly fill context windows. Because of this, we chose to give agents a persistent terminal to work within. Following all the success of agentic programming, we think we can frame EHR tasks the same way.

    - Right now we have naive FHIR get/post tools, but we think it might be best to develop a FHIR REPL. When making a FHIR read request, for example, it might be best to give the agent the ability to peek at the results, e.g., show it the line/char count and head of the result, and let it decide what to do from there. If the result is a 10,000 line json object, it would be best to either pipe it directly into a python process or save it as a json file for further processing, instead of trying to read it.

    - Notepads (like in Claude plays Pokemon, [here](https://x.com/omarsar0/status/1961073840706203804), or even [here](https://x.com/EyubogluSabri/status/1932106746446905552) if we wanted to store very detailed information inside a custom model) to store additional context could help as long as no data is leaked between patients (resource type/ID whitelisting and blacklisting might help with this). Working inside a terminal would naturally allow the model to write text files as notes to itself. It could create folders to store specific resource types with detailed names, instructions, and files for processing them. While this might not be necessary for simpler tasks, we imagine it might aid in long-horizon tasks or "deep-research"-like patient care tasks. In the future, maybe we give the model a locked-down workdir with external API restrictions that only allows it to work with a specified resource type (e.g., a specific doctor visit or the entire patient record). Maybe these workdirs could be stored persistently as a service that could be revisited for further work in future sessions. Imagine if each patient had a dedicated assistant... but Synthea isn't the right dataset for that!

    - We also think that FHIR validation can analogously serve as a weak compiler/interpreter by providing agents feedback.


4) **Add more tasks**:
  For now, the focus is on short-horizon CRUD tasks within an EHR. But, it would be nice to include all parts of the stack, from patient-doctor interactions to prior auth, to live up to the name MedSchool :)

    - **Patient-doctor conversation**: To probe the patient to illicit the right information like [here](https://arxiv.org/pdf/2406.00922). This is a relatively open-ended task, but recent rubric RLVR has been moving in the right direction to enable this. From this, you get a transcript. 

    - **Clinical notes and/or transcript to discrete fields**: To translate clinical notes or transcripts into FHIR resources and enter them into the EHR. Some related work can be found [here](https://arxiv.org/pdf/2306.02022). This would yield clear, verifiable outputs that could be RLVR'd

    - **Calculations**: To learn some clinical math like in [here](https://arxiv.org/pdf/2406.12036). While this may not be the most relevant for operational EHR tasks, it would be useful for later longitudinal care tasks, and has verifiable rewards.

    - **Further multi-step tasks**
    In general, it would be nice to figure out a framework for curriculum learning inside an EHR. The simplest way we've thought of, so far, is by calculating the minimum path length between FHIR resources required to solve a problem. For example, given the task "convert the SNOMED code found inside <resourceType_id> to ICD-10", the agent would need to at least query the resource, and then follow the reference inside of it to query the patient resource to get the necessary information. This is a relatively simple task. It would be nice to figure out a way to automatically determine the path length of any task. Then when training, you could start with simpler tasks and move to harder, more complex ones.
 

5) **Develop a synthetic EHR dataset 🦛**
  This one's a longshot... but, the main problem with Synthea dataset that we use is that it's too synthetic for real, longitudinal clinical tasks. And using any real EHR data requires restrictive licenses that prevent disseminating generative models trained on them, to protect patient confidentiality. But we think it might be possible to get around this now!

    - **Naive approach 🤓:**
      - What if you just prompt an LLM to generate synthetic EHR data? LLMs hold some clinical knowledge, and this might result in a dataset that is more realistic than the base Synthea data. It might be relatively cheap to do with open models and could be openly released, but you're not guaranteed clinically accurate data, especially for long-tail data.
      - So, what if you just rephrased an existing EHR dataset like MIMIC? While synthetic data generation [recipes have improved](https://arxiv.org/pdf/2508.10975), too much identifiable patient data would live within.
    
    - **Cool approach 🤑:**
      - What if you trained a clinical event model, while taking user-level differential privacy measures? Instead of training on raw EHR data, you could lay out a timeline of curated patient data and assign each clinical event a token. Then, train a model to predict the next clinical event token with differential privacy (DP could also be used when curating the initial dataset). And finally, sample 100 or so patients from this, expand each event token trajectory back into FHIR resources, verify that it doesn't contain any PHI, and (hopefully) release it. The act of compressing and decompressing data into clinical events, given enough patient data with DP provisions, might be enough to anonymize it.

      - But is this even possible? Well, Epic recently released a [paper](https://arxiv.org/pdf/2508.12104) finding scaling laws in clinical event models, and Google just trained a [model](https://research.google/blog/vaultgemma-the-worlds-most-capable-differentially-private-llm/) with differential privacy that shows no memorization of, admittedly short, 50-token sequences. You do the math... 
      
      - We don't think this has been tried before, and is at least worth studying. We don't have the means to do this by ourselves, much less the capacity to test or release the data with any high-level of confidence, but if anybody's interested 😅