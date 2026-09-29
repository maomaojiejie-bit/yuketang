"""数据模型测试。"""

from __future__ import annotations

import re

import pytest

from cjsolver.models import (
    CHOICE_TYPES,
    OPTION_LINE_PATTERN,
    Option,
    Problem,
    ProblemType,
    index_for_letter,
    infer_problem_type,
    letter_for,
    parse_problem_type,
)


@pytest.mark.parametrize(
    ("index", "letter"),
    [(0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA")],
)
def test_letter_for(index: int, letter: str) -> None:
    assert letter_for(index) == letter
    assert index_for_letter(letter) == index


def test_index_for_letter_rejects_garbage() -> None:
    assert index_for_letter("") is None
    assert index_for_letter("1") is None
    assert index_for_letter("A1") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, ProblemType.SINGLE_CHOICE),
        (2, ProblemType.MULTIPLE_CHOICE),
        (3, ProblemType.POLL),
        (4, ProblemType.FILL_BLANK),
        (5, ProblemType.SUBJECTIVE),
        (0, ProblemType.UNKNOWN),
        (99, ProblemType.UNKNOWN),
        ("2", ProblemType.MULTIPLE_CHOICE),
        ("single-choice", ProblemType.SINGLE_CHOICE),
        ("single_choice", ProblemType.SINGLE_CHOICE),
        ("MultipleChoice", ProblemType.MULTIPLE_CHOICE),
        ("fill", ProblemType.FILL_BLANK),
        (None, ProblemType.UNKNOWN),
        (True, ProblemType.UNKNOWN),
    ],
)
def test_parse_problem_type(raw: object, expected: ProblemType) -> None:
    assert parse_problem_type(raw) == expected


def test_problem_from_raw_requires_id() -> None:
    assert Problem.from_raw({}) is None
    assert Problem.from_raw({"prompt": "无 id"}) is None
    assert Problem.from_raw("not a dict") is None  # type: ignore[arg-type]


def test_problem_from_raw_handles_string_and_dict_options() -> None:
    problem = Problem.from_raw(
        {
            "problem_id": "abc",
            "problem_type": 1,
            "body": "题干内容",
            "options": [{"text": "甲"}, "乙", {"value": "丙"}, {"nope": "x"}],
        }
    )
    assert problem is not None
    assert problem.id == "abc"
    assert problem.prompt == "题干内容"
    assert [option.text for option in problem.options] == ["甲", "乙", "丙"]
    assert [option.letter for option in problem.options] == ["A", "B", "C"]


def test_problem_helpers() -> None:
    problem = Problem.from_raw(
        {"problemId": 7, "problemType": 1, "prompt": "x", "options": ["进程", "线程"]}
    )
    assert problem is not None
    assert problem.key == "-:7"
    assert problem.letter_to_option("B") is problem.options[1]  # type: ignore[index]
    assert problem.letter_to_option("Z") is None
    assert problem.find_option_by_text(" 线 程 ") is problem.options[1]  # type: ignore[index]
    assert problem.find_option_by_text("不存在") is None


def test_needs_vision_detection() -> None:
    blank_prompt = Problem(id="1", prompt="", image_url="https://x/a.png")
    assert blank_prompt.needs_vision is True

    with_prompt = Problem(id="2", prompt="有题干", image_url="https://x/a.png")
    assert with_prompt.needs_vision is False


def test_already_answered_variants() -> None:
    assert Problem(id="1", raw={"result": ["A"]}).already_answered is True
    assert Problem(id="1", raw={"result": []}).already_answered is False
    assert Problem(id="1", raw={}).already_answered is False


def test_infer_problem_type() -> None:
    assert infer_problem_type("多选题：以下正确的是", [Option(0, "甲"), Option(1, "乙")]) is (
        ProblemType.MULTIPLE_CHOICE
    )
    assert infer_problem_type("判断题", [Option(0, "正确"), Option(1, "错误")]) is (
        ProblemType.SINGLE_CHOICE
    )
    assert infer_problem_type("填空题：___", []) is ProblemType.FILL_BLANK
    assert infer_problem_type("简述进程与线程的区别", []) is ProblemType.SUBJECTIVE
    assert infer_problem_type("没有关键字", [Option(0, "甲"), Option(1, "乙")]) is (
        ProblemType.SINGLE_CHOICE
    )


def test_option_line_pattern_supports_more_than_five_options() -> None:
    """选项字母不能只认到 E：第 6 个之后的选项也要能解析出来。"""
    assert re.match(OPTION_LINE_PATTERN, "A. 甲") is not None
    ninth = re.match(OPTION_LINE_PATTERN, "I. 第九个选项")
    assert ninth is not None
    assert ninth.group(1) == "I"
    assert re.match(OPTION_LINE_PATTERN, "1. 不是选项") is None
    assert re.match(OPTION_LINE_PATTERN, "A 没有分隔符") is None


def test_choice_types_membership() -> None:
    assert ProblemType.SINGLE_CHOICE in CHOICE_TYPES
    assert ProblemType.MULTIPLE_CHOICE in CHOICE_TYPES
    assert ProblemType.FILL_BLANK not in CHOICE_TYPES
