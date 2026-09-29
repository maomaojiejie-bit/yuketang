"""手动粘贴题目的解析测试。"""

from __future__ import annotations

from cjsolver.adhoc import parse_adhoc_problem
from cjsolver.models import ProblemType


def test_parses_single_choice() -> None:
    problem = parse_adhoc_problem(
        "单选 下列关于进程的说法正确的是\nA. 进程是资源分配的基本单位\nB. 线程是资源分配的基本单位\n"
    )
    assert problem.type is ProblemType.SINGLE_CHOICE
    assert problem.prompt == "下列关于进程的说法正确的是"
    assert [option.text for option in problem.options] == [
        "进程是资源分配的基本单位",
        "线程是资源分配的基本单位",
    ]


def test_parses_multiple_choice_with_chinese_punctuation() -> None:
    problem = parse_adhoc_problem("多选 以下属于调度算法的有\nA、先来先服务\nB．短作业优先\nC）时间片轮转\n")
    assert problem.type is ProblemType.MULTIPLE_CHOICE
    assert len(problem.options) == 3
    assert problem.options[1].text == "短作业优先"


def test_parses_fill_blank() -> None:
    problem = parse_adhoc_problem("填空 页式存储中完成地址映射的部件是\n第1空：页表\n")
    assert problem.type is ProblemType.FILL_BLANK
    assert problem.blanks == ["页表"]


def test_prompt_keeps_multiline_text() -> None:
    problem = parse_adhoc_problem("阅读下列材料\n然后回答问题\nA. 甲\nB. 乙\n")
    assert problem.prompt == "阅读下列材料\n然后回答问题"
    assert len(problem.options) == 2


def test_stable_id_across_calls() -> None:
    text = "单选 题\nA. 甲\nB. 乙\n"
    assert parse_adhoc_problem(text).id == parse_adhoc_problem(text).id


def test_parses_more_than_five_options() -> None:
    """9 个选项（A-I）的单选/多选不能再被截断成 8 个。"""
    lines = ["多选 下列哪些属于操作系统"]
    lines += [
        "%s. 选项%d" % (chr(ord("A") + index), index + 1) for index in range(9)
    ]
    problem = parse_adhoc_problem("\n".join(lines))

    assert problem.type is ProblemType.MULTIPLE_CHOICE
    assert len(problem.options) == 9
    assert [option.letter for option in problem.options] == list("ABCDEFGHI")
    assert problem.options[-1].text == "选项9"


def test_single_choice_with_six_options() -> None:
    """超过 5 个选项的单选题同样要完整识别。"""
    lines = ["单选 下列哪个是正确答案"]
    lines += [
        "%s. 选项%d" % (chr(ord("A") + index), index + 1) for index in range(6)
    ]
    problem = parse_adhoc_problem("\n".join(lines))

    assert problem.type is ProblemType.SINGLE_CHOICE
    assert len(problem.options) == 6
    assert problem.options[-1].letter == "F"


def test_empty_input_yields_empty_problem() -> None:
    problem = parse_adhoc_problem("")
    assert problem.prompt == ""
    assert problem.options == []
