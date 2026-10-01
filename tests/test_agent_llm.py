import json

import httpx
import pytest
import respx
from prometheus_client import REGISTRY

from govwatch.agent.llm import LLMError, OllamaClient
from govwatch.agent.prompt import OUTPUT_SCHEMA, build_prompt

URL = "http://ollama.test"
MODEL = "test-model"
PROMPT = build_prompt("Some Act", "in_committee", "This bill does a thing.")


def ok(output, prompt_tokens=120, completion_tokens=40):
    return httpx.Response(
        200,
        json={
            "response": json.dumps(output),
            "prompt_eval_count": prompt_tokens,
            "eval_count": completion_tokens,
        },
    )


@pytest.fixture
def llm():
    client = OllamaClient(URL, MODEL, sleep=lambda _: None)
    yield client
    client.close()


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


@respx.mock
def test_sends_schema_for_constrained_decoding(llm):
    route = respx.post(f"{URL}/api/generate").mock(
        return_value=ok({"summary": "x", "policy_area": "Health"})
    )
    gen = llm.generate(PROMPT, OUTPUT_SCHEMA)

    body = json.loads(route.calls.last.request.content)
    assert body["format"] == OUTPUT_SCHEMA
    assert body["model"] == MODEL
    assert body["options"]["temperature"] == 0
    assert body["system"] == PROMPT.system
    assert gen.output == {"summary": "x", "policy_area": "Health"}
    assert (gen.prompt_tokens, gen.completion_tokens) == (120, 40)


@respx.mock
def test_records_tokens(llm):
    before = sample("govwatch_llm_tokens_total", model=MODEL, kind="completion")
    respx.post(f"{URL}/api/generate").mock(return_value=ok({"summary": "x"}, completion_tokens=7))
    llm.generate(PROMPT, OUTPUT_SCHEMA)
    assert sample("govwatch_llm_tokens_total", model=MODEL, kind="completion") == before + 7


@respx.mock
def test_retries_a_500_then_succeeds(llm):
    # ollama answers 500 when a model fails to load, e.g. out of memory
    route = respx.post(f"{URL}/api/generate").mock(
        side_effect=[httpx.Response(500, text="model failed to load"), ok({"summary": "x"})]
    )
    assert llm.generate(PROMPT, OUTPUT_SCHEMA).output == {"summary": "x"}
    assert route.call_count == 2


@respx.mock
def test_does_not_retry_a_404(llm):
    route = respx.post(f"{URL}/api/generate").mock(
        return_value=httpx.Response(404, text='model "test-model" not found')
    )
    with pytest.raises(LLMError, match="404"):
        llm.generate(PROMPT, OUTPUT_SCHEMA)
    assert route.call_count == 1


@respx.mock
def test_gives_up_when_ollama_is_down(llm):
    route = respx.post(f"{URL}/api/generate").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMError, match="unreachable"):
        llm.generate(PROMPT, OUTPUT_SCHEMA)
    assert route.call_count == llm.max_retries + 1


@respx.mock
def test_non_json_output_is_an_error(llm):
    respx.post(f"{URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "Sure! Here is a summary:"})
    )
    with pytest.raises(LLMError, match="wasn't JSON"):
        llm.generate(PROMPT, OUTPUT_SCHEMA)
