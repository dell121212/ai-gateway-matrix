from dashboard.app.services.stream_parser import StreamAccumulator, parse_sse_buffer


def test_parse_content_and_usage():
    acc = StreamAccumulator()
    buf = (
        'data: {"id":"1","choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}]}\n\n'
        'data: {"usage":{"prompt_tokens":10,"completion_tokens":2},"model":"gpt-test"}\n\n'
        "data: [DONE]\n\n"
    )
    parse_sse_buffer(buf, acc)
    assert acc.content_chars == len("Hello") + len(" world")
    assert acc.first_token_seen
    assert acc.usage_from_upstream
    assert acc.prompt_tokens == 10
    assert acc.completion_tokens == 2
    assert acc.model == "gpt-test"
    assert acc.finish_reason == "stop"


def test_tool_call_args_counted():
    acc = StreamAccumulator()
    parse_sse_buffer(
        'data: {"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"{\\"a\\":1}"}}]}}]}\n',
        acc,
    )
    assert acc.tool_arg_chars > 0
    assert acc.first_token_seen


def test_estimate_tokens_from_chars():
    acc = StreamAccumulator()
    acc.content_chars = 40
    assert acc.estimated_completion_tokens() == 10
