# various rubrics taken from docs to store for now 

def exact_match(prompt, completion, answer, state):
    """Reward exact matches."""
    response = completion[-1]['content']
    return 1.0 if response.strip() == answer.strip() else 0.0

def correctness(prompt, completion, answer, state):
    return 1.0 if answer.lower() in completion[-1]['content'].lower() else 0.0

def correct_answer(parser, completion, answer):
    response = parser.parse_answer(completion) or ''
    return 1.0 if response.strip() == answer.strip() else 0.0

def correct_answer(parser, completion, answer):
    parsed = parser.parse_answer(completion) # applies extract_fn to final message
    return 1.0 if parsed == answer else 0.0

def partial_credit(prompt, completion, answer, state):
    """Give partial credit for containing key terms."""
    key_terms = answer.lower().split()
    response = completion[-1]['content']
    found = sum(1 for term in key_terms if term in response.lower())
    return found / len(key_terms) if key_terms else 0.0

