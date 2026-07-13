"""Build a partially ordered English vocabulary from a frequency-ranked corpus.

Downloads a frequency-descending word list, cleans it, groups it into ordered
acquisition tiers (a poset: tiers are ordered, words within a tier are
incomparable), and saves the structure to english_poset_vocabulary.json.

Usage:
    python scripts/build_english_poset.py
"""
import json
import urllib.request


def download_and_process_lexicon():
    print("Connecting to public lexical repositories...")

    # Google Web Trillion Word Corpus unigram counts (Norvig), ~333k words
    # ordered by frequency descending, one "word<TAB>count" per line.
    url = "https://norvig.com/ngrams/count_1w.txt"
    req = urllib.request.Request(url, headers={"User-Agent": "bible-reader/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw_words = r.read().decode("utf-8").splitlines()

    # Clean the vocabulary (remove duplicates, short noise, and format strings)
    seen = set()
    cleaned_words = []
    for line in raw_words:
        word_clean = line.split()[0].strip().lower() if line.split() else ""
        if word_clean.isalpha() and len(word_clean) > 1 and word_clean not in seen:
            seen.add(word_clean)
            cleaned_words.append(word_clean)

    print(f"Successfully processed {len(cleaned_words)} core common-usage words.")
    return cleaned_words


def build_partial_ordering(word_list):
    """
    Groups words into ordered structural tiers.
    Words within the same tier are mutually incomparable (Partial Ordering),
    while Tier N strictly precedes Tier N+1.
    """
    # Define acquisition milestones based on language acquisition curves
    tier_thresholds = [
        ("Tier 1: Core Functional", 2000),
        ("Tier 2: General Fluency", 5000),
        ("Tier 3: Advanced Academic", 10000),
        ("Tier 4: Collegiate Threshold", 20000),
        ("Tier 5: Scholarly Native Lexicon", 40000),
        ("Tier 6: Hyper-Specialized", len(word_list)),
    ]

    poset_database = {}
    previous_index = 0

    for tier_name, limit in tier_thresholds:
        # Prevent index errors if dataset variation occurs
        current_limit = min(limit, len(word_list))

        # This slice represents an Antichain (elements are incomparable within the subset)
        words_in_tier = word_list[previous_index:current_limit]

        poset_database[tier_name] = {
            "metadata": f"Words ranked {previous_index + 1} to {current_limit}",
            "elements": words_in_tier,
        }

        previous_index = current_limit

    return poset_database


def check_partial_order_relation(word_a, word_b, word_list):
    """
    Mathematical evaluation of the poset.
    Returns True if word_a strictly must be learned BEFORE word_b.
    Returns False if they are incomparable or if word_b comes first.
    """
    try:
        idx_a = word_list.index(word_a.lower().strip())
        idx_b = word_list.index(word_b.lower().strip())

        # Defining relations across broad frequency thresholds (e.g., blocks of 1000 words)
        block_a = idx_a // 1000
        block_b = idx_b // 1000

        if block_a < block_b:
            return f"'{word_a}' strictly precedes '{word_b}' (Precedes in learning order)"
        elif block_a > block_b:
            return f"'{word_b}' strictly precedes '{word_a}' (Follows in learning order)"
        else:
            return f"'{word_a}' ∥ '{word_b}' (Incomparable: Same learning acquisition block)"

    except ValueError:
        return "One or both words are outside the common 70,000 word spectrum."


# --- Execution ---
if __name__ == "__main__":
    # 1. Download and clean the corpus
    ordered_vocabulary = download_and_process_lexicon()

    # 2. Structure mathematically into a Poset Map
    word_poset = build_partial_ordering(ordered_vocabulary)

    # 3. Save organized dataset to disk
    with open("english_poset_vocabulary.json", "w", encoding="utf-8") as f:
        json.dump(word_poset, f, indent=4)
    print("Saved structured partial-ordering to 'english_poset_vocabulary.json'.")

    # 4. Demonstrate Mathematical Relations Evaluator
    print("\n--- Poset Mathematical Relationship Examples ---")
    print(check_partial_order_relation("the", "molecule", ordered_vocabulary))
    print(check_partial_order_relation("molecule", "the", ordered_vocabulary))
    print(check_partial_order_relation("analyze", "evaluate", ordered_vocabulary))
