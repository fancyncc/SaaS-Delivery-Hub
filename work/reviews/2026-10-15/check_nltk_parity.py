"""Capture old scoring before dependency removal, then verify the replacement."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ["NLTK_DATA"] = str(ROOT / "data/evaluations/nltk_data")
from backend.cmrc_evaluation import answer_scores, mixed_segmentation, score_run

EVIDENCE = Path(__file__).parent / "evidence"
BASELINE = EVIDENCE / "nltk-parity-baseline.json"
CORPUS = ROOT / "data/evaluations/cmrc2018-200"
RUN = ROOT / "data/evaluations/cmrc-200-final-20261012/run.json"
ENGLISH = [
    "", "Hello world. Next sentence.", "Dr. Smith met Mr. Jones at 3.14 p.m. He left.",
    "I can't, won't and shouldn't. They've said we're ready!", "U.S.A. vs. U.K. etc. A. Bach",
    "\"Hello.\" (Next sentence.) [End!]", "‘Quoted’ “words.” «More!»", "... .... ?! !?!",
    "1,000.25 12:30 50% $2.99 user@example.com https://example.com/a-b",
    "cannot gonna wanna gimme gotta lemme d'ye more'n 'tis 'twas",
    "第一版v1.2.3在2026年发布。English text. Next sentence!", "a\r\nb\t c\n\nDr. No.",
    "🙂 café naïve coöperate Ελληνικά 日本語 한국어", "e.g. i.e. Inc. Jan. St. vs. etc.",
    "alpha-beta gamma_delta a/b a\\b", "\"a\" 'b' ``c'' -- d—e … end.",
]


def capture():
    import nltk
    import random
    data = json.loads((CORPUS / "cmrc2018_dev.json").read_bytes())
    texts, pairs = set(ENGLISH), []
    for article in data["data"]:
        for paragraph in article["paragraphs"]:
            texts.add(paragraph["context"])
            for qa in paragraph["qas"]:
                texts.add(qa["question"])
                answers = [a["text"] for a in qa["answers"]]
                texts.update(answers)
                answer = answers[0]
                for prediction in [answer, answer[1:], answer[::2], answer + " extra. Next sentence!"]:
                    pairs.append([answers, prediction, answer_scores(answers, prediction)])
    recording = json.loads(RUN.read_bytes())
    texts.update(row.get("answer", "") for row in recording["rows"])
    rng = random.Random(2018)
    for _ in range(1000):
        texts.add(" ".join(rng.choices(ENGLISH[1:], k=rng.randrange(1, 5))))
    payload = {
        "reference_nltk_version": nltk.__version__,
        "mixed_tokens": [[text, mixed_segmentation(text)] for text in sorted(texts)],
        "word_tokens": [[text, nltk.word_tokenize(text)] for text in sorted(texts)],
        "score_pairs": pairs,
        "historical_score": score_run(CORPUS, recording),
    }
    with BASELINE.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False)
    print(json.dumps({"texts": len(texts), "score_pairs": len(pairs),
                      "historical_questions": len(recording["rows"])}))


def verify():
    from backend.benchmark_tokenizer import word_tokenize
    payload = json.loads(BASELINE.read_bytes())
    mismatches = []
    for key, tokenizer in [("mixed_tokens", mixed_segmentation), ("word_tokens", word_tokenize)]:
        for text, expected in payload[key]:
            if tokenizer(text) != expected:
                mismatches.append({"kind": key, "text": text[:150]})
    for answers, prediction, expected in payload["score_pairs"]:
        if answer_scores(answers, prediction) != expected:
            mismatches.append({"kind": "score", "prediction": prediction[:150]})
    current = score_run(CORPUS, json.loads(RUN.read_bytes()))
    old = payload["historical_score"]
    current.pop("scoring")
    old.pop("scoring")
    if current != old:
        mismatches.append({"kind": "historical_score"})
    result = {"reference_nltk_version": payload["reference_nltk_version"],
              "text_cases": len(payload["mixed_tokens"]),
              "tokenization_comparisons": len(payload["mixed_tokens"]) * 2,
              "score_pairs": len(payload["score_pairs"]),
              "historical_questions": current["question_count"],
              "historical_metrics": current["metrics"], "mismatches": mismatches}
    (EVIDENCE / "nltk-parity-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    assert not mismatches


if __name__ == "__main__":
    capture() if sys.argv[1] == "baseline" else verify()
