def efficiency_reward(prompt, response, answer, state):
    """Reward based on number of steps taken."""
    max_steps = 10
    steps_taken = state.get("turn", 0)
    return max(0, (max_steps - steps_taken) / max_steps)