"""模型输出解析与答案归一化测试。"""

from __future__ import annotations

import json

from cjsolver.ai.prompts import build_messages, normalize_answer, parse_suggestion
from cjsolver.models import Option, Problem, ProblemType


def make_problem(problem_type: ProblemType, options: list[str] | None = None) -> Problem:
    return Problem(
        id="p1",
        type=problem_type,
        prompt="题干",
        options=[Option(index=i, text=text) for i, text in enumerate(options or [])],
    )


def test_single_choice_json_answer() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙", "丙", "丁"])
    raw = json.dumps({"answer": "C", "explanation": "因为…", "confidence": 0.9})
    suggestion = parse_suggestion(problem, raw)

    assert suggestion.letters == ["C"]
    assert suggestion.explanation == "因为…"
    assert suggestion.confidence == 0.9
    assert suggestion.submit_payload(problem) == ["C"]


def test_single_choice_keeps_only_first_letter() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙", "丙"])
    suggestion = parse_suggestion(problem, json.dumps({"answer": "AC"}))
    assert suggestion.letters == ["A"]


def test_multiple_choice_sorts_and_dedupes() -> None:
    problem = make_problem(ProblemType.MULTIPLE_CHOICE, ["甲", "乙", "丙", "丁"])
    suggestion = parse_suggestion(problem, json.dumps({"answer": ["C", "a", "C"]}))
    assert suggestion.letters == ["A", "C"]
    assert suggestion.submit_payload(problem) == ["A", "C"]


def test_choice_answer_given_as_option_text() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["进程", "线程", "协程"])
    suggestion = parse_suggestion(problem, json.dumps({"answer": "线程"}))
    assert suggestion.letters == ["B"]


def test_letters_out_of_range_are_dropped() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙"])
    suggestion = parse_suggestion(problem, json.dumps({"answer": "E"}))
    assert suggestion.letters == []
    assert suggestion.answerable is False


def test_answers_beyond_e_are_accepted() -> None:
    """选项超过 5 个时，模型给出的 F/G/H 不能被当成越界字母丢掉。"""
    options = ["选项%s" % chr(ord("A") + i) for i in range(8)]
    multiple = make_problem(ProblemType.MULTIPLE_CHOICE, options)
    suggestion = parse_suggestion(multiple, json.dumps({"answer": ["A", "H"]}))
    assert suggestion.letters == ["A", "H"]
    assert suggestion.submit_payload(multiple) == ["A", "H"]

    single = make_problem(ProblemType.SINGLE_CHOICE, options)
    assert parse_suggestion(single, json.dumps({"answer": "G"})).letters == ["G"]


def test_fill_blank_splits_text() -> None:
    problem = make_problem(ProblemType.FILL_BLANK)
    suggestion = parse_suggestion(problem, json.dumps({"answer": "页表，段表", "confidence": "80%"}))
    assert suggestion.texts == ["页表", "段表"]
    assert suggestion.confidence == 0.8
    assert suggestion.submit_payload(problem) == ["页表", "段表"]


def test_subjective_payload_shape() -> None:
    problem = make_problem(ProblemType.SUBJECTIVE)
    suggestion = parse_suggestion(problem, json.dumps({"answer": "先这样做，再那样做。"}))
    assert suggestion.submit_payload(problem) == {
        "content": "先这样做，再那样做。",
        "pics": [],
    }


def test_fenced_json_is_accepted() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙"])
    raw = '```json\n{"answer": "B", "explanation": "ok"}\n```'
    assert parse_suggestion(problem, raw).letters == ["B"]


def test_json_with_surrounding_prose() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙"])
    raw = '好的，分析如下：\n{"answer": "A", "explanation": "含 { 花括号 } 的说明"}\n以上。'
    suggestion = parse_suggestion(problem, raw)
    assert suggestion.letters == ["A"]
    assert "花括号" in suggestion.explanation


def test_legacy_plain_text_fallback() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙"])
    raw = "答案：B\n解析：因为 B 正确。"
    suggestion = parse_suggestion(problem, raw)
    assert suggestion.letters == ["B"]
    assert suggestion.explanation == "因为 B 正确。"


def test_null_answer_reports_failure_reason() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙"])
    raw = json.dumps({"answer": None, "failureReason": "题干信息不足"})
    suggestion = parse_suggestion(problem, raw)
    assert suggestion.answerable is False
    assert suggestion.failure_reason == "题干信息不足"


def test_normalize_answer_accepts_dict_and_numbers() -> None:
    blank = make_problem(ProblemType.FILL_BLANK)
    assert normalize_answer(blank, {"content": "甲\n乙"}) == ([], ["甲", "乙"])
    # 纯数字不是选择题的合法答案：0 基 / 1 基存在歧义，宁可作废也不猜错
    assert normalize_answer(make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙"]), 1) == ([], [])
    # 但填空题里的数字是正常答案
    assert normalize_answer(blank, 42) == ([], ["42"])
    assert normalize_answer(blank, None) == ([], [])


def test_build_messages_includes_options_and_format_hint() -> None:
    problem = make_problem(ProblemType.MULTIPLE_CHOICE, ["甲", "乙"])
    messages = build_messages(problem, extra_prompt="简短一些")

    assert messages[0]["role"] == "system"
    assert "JSON" in messages[0]["content"]
    user = messages[1]["content"]
    assert "多选题" in user
    assert "A. 甲" in user
    assert "数组" in user
    assert "额外要求：简短一些" in user


def test_build_messages_with_images_uses_content_parts() -> None:
    problem = make_problem(ProblemType.SINGLE_CHOICE, ["甲", "乙"])
    messages = build_messages(problem, images=["https://example.com/a.png"])
    content = messages[1]["content"]

    assert isinstance(content, list)
    assert content[0] == {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    assert content[-1]["type"] == "text"
