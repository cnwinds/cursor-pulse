from pulse.extract.vision_parser import parse_vision_response


SAMPLE_VISION_JSON = """
{
  "confidence": 0.82,
  "warnings": ["部分 token 列不可见"],
  "records": [
    {
      "date": "2026-06-17T07:10:03.037Z",
      "kind": "Included",
      "model": "auto",
      "max_mode": false,
      "input_with_cache_write": 0,
      "input_without_cache_write": 22392,
      "cache_read": 49696,
      "output_tokens": 1394,
      "total_tokens": 73482,
      "cost": "Included"
    }
  ]
}
"""


def test_parse_vision_response():
    result = parse_vision_response(SAMPLE_VISION_JSON)
    assert result.confidence == 0.82
    assert len(result.records) == 1
    assert result.records[0].model == "auto"
    assert result.records[0].tokens_total == 73482
    assert result.summary.event_count == 1
    assert "部分 token 列不可见" in result.warnings
