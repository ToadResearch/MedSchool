# MedSchool 🩺 🤗 
---

> Imagine if all major coding benchmarks were multiple-choice QA

That's the current state of affairs for medical/clinical benchmarking, outside of maybe the new conversational HealthBench that has the big lab spotlight on it right now.

Medical/clinical data is semi-verifiable: it's a mix of objective and subjective statements, free-text and discrete fields, etc. EHRs store all this data and serve as ground truth repositories for documented clinical realities, and because they are highly structured, they are implicitly verifiable. But to the best of our knowledge, there have been no public works applying RLVR to agentic EHR tasks. We aim to change that!

We think that EHRs are the gateway to most clinical tasks. From what we've seen with programming, we believe the best way to train clinical intelligence is by giving models the ability to take action and learn from experience within EHR environments. Some more about *the plan* can be found [here](https://x.com/mkieffer1107/status/1958644405411225788). We might spruce this up in the future and turn it into a blog post.

---
### Want to help?

![Under construction](assets/under_construction.gif)

> This project is actively under development and there are many known bugs!


Right now we only have basic MCP support, and are beginning to work on the environment itself. The two biggest challenges to solve:

1) Figure out the minimal MCP toolset to best handle EHR tasks
2) Figure out how to generate env tasks automatically

If you're interested in clinical intelligence, developing realistic health/medical benchmarks, or creating an open-source copilot for doctors, consider helping out!

---

### Current tools available:

- **fhir_query**: read FHIR records
- **python_exec**: execute a python
- **shell_exec**: execute shell command

Note: It might be better to migrate most work into a shell because FHIR records are very large json objects that quickly fill context windows. For example, if you're doing payment analysis over a patient record, it might be best to pipe FHIR query results directly into a python process, rather than wasting context to copy and paste it in. This is especially a problem when running models locally.

---

### Setup:

1.  **`cp .env.example .env`**
    *   This copies the example environment configuration file to a new `.env` file that you will use.

2.  **Update `JWT_SHARED_SECRET` in `.env`**
    *   Open the `.env` file and change the value of `JWT_SHARED_SECRET` to a unique, random string.

3.  **Run `./docker/nginx/scripts/generate_jwt.sh` to get a JWT**
    *   This script creates the bearer token needed to authenticate with the FHIR server. You have a few options for how the token is generated:
        *   **Default (24-hour expiration):** Running the script with no flags creates a token that expires in 24 hours.
          ```sh
          ./docker/nginx/scripts/generate_jwt.sh
          ```
        *   **Custom Expiration:** Use the `--expires-in <hours>` flag to specify a different lifespan.
          ```sh
          # Example: Create a token that lasts for one week (168 hours)
          ./docker/nginx/scripts/generate_jwt.sh --expires-in 168
          ```
        *   **No Expiration (For Demos):** For public demos with non-sensitive data, you can create a token that never expires using the `--no-expiry` flag.
          ```sh
          ./docker/nginx/scripts/generate_jwt.sh --no-expiry
          ```

4.  **Copy the generated token into `FHIR_BEARER_TOKEN`**
    *   After running the script, paste the output token into the `FHIR_BEARER_TOKEN` variable in your `.env` file.

5.  **Run `./startup.sh --synthea`**
    *   This command starts all the Docker services and runs a job to download and load synthetic patient data into the server.

6. **Add the MCP client config to your `mcp.json` of choice**
    ```json
    {
      "medschool-mcp": {
        "url": "http://127.0.0.1:8000/mcp"
      }
    }
    ```

---

### How to remove everything:

To stop all services and completely delete all containers, data volumes, and associated images, run the purge command:
```sh
./shutdown.sh --purge
```

