"""解析层测试：从雨课堂真实形状的载荷中提取题目。"""

from __future__ import annotations

from conftest import load_fixture

from cjsolver.models import ProblemType
from cjsolver.yuketang.parsing import extract_problems, looks_like_problem


def test_extracts_problems_from_presentation() -> None:
    payload = load_fixture("presentation_sample.json")
    problems = extract_problems(payload, lesson_id="lesson-abc")

    assert len(problems) == 3
    by_id = {problem.id: problem for problem in problems}
    assert set(by_id) == {"1234567", "7654321", "1112223"}

    single = by_id["1234567"]
    assert single.type is ProblemType.SINGLE_CHOICE
    assert single.lesson_id == "lesson-abc"
    assert single.presentation_id == "presentation-987654"
    assert single.slide_id == "slide-2"
    assert single.image_url.endswith("/slide/q1.png")
    assert [option.letter for option in single.options] == ["A", "B", "C", "D"]
    assert single.options[0].text == "进程是资源分配的基本单位"
    assert single.needs_vision is False
    assert single.already_answered is False


def test_extracts_options_and_result_variants() -> None:
    payload = load_fixture("presentation_sample.json")
    by_id = {p.id: p for p in extract_problems(payload)}

    multiple = by_id["7654321"]
    assert multiple.type is ProblemType.MULTIPLE_CHOICE
    # answers 是 options 的别名，元素是纯字符串
    assert [option.text for option in multiple.options] == [
        "先来先服务",
        "短作业优先",
        "时间片轮转",
        "银行家算法",
    ]
    # 已有 result 的题目应当被识别为已作答
    assert multiple.already_answered is True

    blank = by_id["1112223"]
    assert blank.type is ProblemType.FILL_BLANK
    assert blank.blanks == ["页表"]


def test_extracts_problem_from_websocket_frame() -> None:
    payload = load_fixture("websocket_sample.json")
    problems = extract_problems(payload, source="websocket")

    assert len(problems) == 1
    problem = problems[0]
    assert problem.id == "9990001"
    assert problem.lesson_id == "lesson-abc"
    assert problem.source == "websocket"
    assert problem.prompt == "HTTP 默认端口是？"
    assert len(problem.options) == 3


def test_looks_like_problem_rejects_unrelated_objects() -> None:
    assert looks_like_problem({"problemId": 1, "problemType": 1}) is True
    assert looks_like_problem({"slides": [], "title": "x"}) is False
    assert looks_like_problem({"id": 5, "name": "张三"}) is False
    assert looks_like_problem("not a dict") is False


def test_extract_problems_on_garbage_returns_empty() -> None:
    assert extract_problems(None) == []
    assert extract_problems({"code": 0, "data": {}}) == []
    assert extract_problems([{"foo": "bar"}]) == []


def test_deduplicates_same_problem_across_walks() -> None:
    payload = {
        "data": {
            "slides": [
                {
                    "id": "s1",
                    "problem": {"problemId": 42, "problemType": 1, "prompt": "?", "options": ["a", "b"]},
                }
            ],
            "extra": {"problemId": 42, "problemType": 1, "prompt": "?", "options": ["a", "b"]},
        }
    }
    problems = extract_problems(payload)
    assert len(problems) == 1
