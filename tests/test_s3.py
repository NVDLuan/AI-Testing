"""Test phân giải URL S3. Chạy: python -m tests.test_s3"""
from adapters.storage import parse_s3_url


def test_parse_s3_uri():
    assert parse_s3_url("s3://my-bucket/path/to/v001.mp4") == ("my-bucket", "path/to/v001.mp4")


def test_parse_virtual_hosted():
    url = "https://my-bucket.s3.ap-southeast-1.amazonaws.com/videos/v002.mp4"
    assert parse_s3_url(url) == ("my-bucket", "videos/v002.mp4")


def test_parse_path_style():
    url = "https://s3.ap-southeast-1.amazonaws.com/my-bucket/videos/v003.mp4"
    assert parse_s3_url(url) == ("my-bucket", "videos/v003.mp4")


def test_parse_plain_key_with_prefix():
    assert parse_s3_url("v004.mp4", default_bucket="b", prefix="raw/clips") == ("b", "raw/clips/v004.mp4")


if __name__ == "__main__":
    test_parse_s3_uri()
    test_parse_virtual_hosted()
    test_parse_path_style()
    test_parse_plain_key_with_prefix()
    print("OK - test S3 parse pass")
