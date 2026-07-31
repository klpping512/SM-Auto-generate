import pytest
import httpx


def test_discover_video_candidates_from_html_and_deduplicate_urls():
    from hotspot_media import discover_media_candidates

    html = """
    <html><head>
      <meta property="og:image" content="/lead.jpg">
      <meta property="og:video" content="https://cdn.example.org/news.mp4">
      <script type="application/ld+json">
        {"@type":"VideoObject","contentUrl":"https://cdn.example.org/second.mp4",
         "thumbnailUrl":"https://cdn.example.org/thumb.jpg","duration":"PT1M2S"}
      </script>
    </head><body>
      <video><source src="https://cdn.example.org/news.mp4" type="video/mp4"></video>
      <iframe src="https://www.youtube.com/embed/abc123def45"></iframe>
    </body></html>
    """

    items = discover_media_candidates(html, "https://news.example.org/story")

    assert [(item["media_kind"], item["platform"]) for item in items] == [
        ("image", "direct"),
        ("video_link", "direct"),
        ("video_link", "direct"),
        ("video_link", "youtube"),
    ]
    assert items[0]["original_media_url"] == "https://news.example.org/lead.jpg"
    assert items[1]["original_media_url"] == "https://cdn.example.org/news.mp4"
    assert items[2]["duration_seconds"] == 62.0
    assert items[3]["original_media_url"] == "https://www.youtube.com/watch?v=abc123def45"
    assert items[3]["platform_media_id"] == "abc123def45"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/@SAtoday",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=abc123def45&list=PL123",
        "http://cdn.example.org/video.mp4",
        "https://localhost/video.mp4",
    ],
)
def test_validate_single_video_url_rejects_channels_playlists_and_unsafe_urls(url):
    from hotspot_media import validate_single_video_url

    with pytest.raises(ValueError):
        validate_single_video_url(url)


def test_validate_single_video_url_normalizes_youtube_short_and_direct_video():
    from hotspot_media import validate_single_video_url

    assert validate_single_video_url("https://youtu.be/abc123def45") == (
        "https://www.youtube.com/watch?v=abc123def45"
    )
    assert validate_single_video_url("https://cdn.example.org/report.mp4") == (
        "https://cdn.example.org/report.mp4"
    )


def test_discover_article_images_filters_chrome_ads_and_duplicates():
    from hotspot_media import discover_media_candidates

    html = """
    <html><body>
      <header><img src="/site-logo.png" alt="Department logo"></header>
      <main><article class="entry-content">
        <img src="/uploads/port-operation.jpg" alt="Port operation">
        <img data-lazy-src="/uploads/minister-roundtable.jpg" alt="Roundtable">
        <img src="/uploads/port-operation.jpg" alt="Duplicate">
        <img src="/ads/summer-banner.jpg" class="advert banner" alt="Advertisement">
        <img src="/authors/avatar.jpg" class="avatar" alt="Author avatar">
      </article></main>
      <footer><img src="/footer-icon.png" alt="Footer icon"></footer>
    </body></html>
    """

    items = discover_media_candidates(html, "https://www.transport.gov.za/news/update")
    images = [item["original_media_url"] for item in items if item["media_kind"] == "image"]

    assert images == [
        "https://www.transport.gov.za/uploads/port-operation.jpg",
        "https://www.transport.gov.za/uploads/minister-roundtable.jpg",
    ]


def test_discover_article_images_limits_page_to_twelve():
    from hotspot_media import discover_media_candidates

    tags = "".join(f'<img src="/uploads/photo-{index}.jpg">' for index in range(20))
    items = discover_media_candidates(
        f"<html><main><article>{tags}</article></main></html>",
        "https://news.example.org/story",
    )

    images = [item for item in items if item["media_kind"] == "image"]
    assert len(images) == 12


def test_discover_article_images_prefers_wordpress_original_image():
    from hotspot_media import discover_media_candidates

    html = """
    <main><article>
      <img src="/uploads/photo-1024x682.jpg"
           srcset="/uploads/photo-640x426.jpg 640w, /uploads/photo-1536x1024.jpg 1536w"
           data-orig-file="/uploads/photo.jpg" alt="Port operation">
      <img src="/uploads/second-300x200.jpg"
           srcset="/uploads/second-640x426.jpg 640w, /uploads/second.jpg 2048w"
           alt="Minister briefing">
    </article></main>
    """

    items = discover_media_candidates(html, "https://news.example.org/story")
    images = [item["original_media_url"] for item in items if item["media_kind"] == "image"]

    assert images == [
        "https://news.example.org/uploads/photo.jpg",
        "https://news.example.org/uploads/second.jpg",
    ]


@pytest.mark.asyncio
async def test_filter_reachable_image_candidates_keeps_videos_and_valid_images():
    from hotspot_media import filter_reachable_image_candidates

    candidates = [
        {"media_kind": "image", "original_media_url": "https://cdn.example.org/good.jpg"},
        {"media_kind": "image", "original_media_url": "https://cdn.example.org/bad.jpg"},
        {"media_kind": "image", "original_media_url": "https://cdn.example.org/html.jpg"},
        {"media_kind": "image", "original_media_url": "https://cdn.example.org/head-blocked.jpg"},
        {
            "media_kind": "image",
            "original_media_url": "https://cdn.example.org/recover-1024x682.jpg",
            "thumbnail_url": "https://cdn.example.org/recover-1024x682.jpg",
        },
        {"media_kind": "video_link", "original_media_url": "https://cdn.example.org/news.mp4"},
    ]

    async def handler(request: httpx.Request):
        if request.url.path == "/good.jpg":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, request=request)
        if request.url.path == "/bad.jpg":
            return httpx.Response(502, headers={"content-type": "text/html"}, request=request)
        if request.url.path == "/html.jpg":
            return httpx.Response(200, headers={"content-type": "text/html"}, request=request)
        if request.url.path == "/head-blocked.jpg" and request.method == "HEAD":
            return httpx.Response(405, request=request)
        if request.url.path == "/head-blocked.jpg":
            return httpx.Response(206, headers={"content-type": "image/webp"}, request=request)
        if request.url.path == "/recover-1024x682.jpg":
            return httpx.Response(502, headers={"content-type": "text/html"}, request=request)
        if request.url.path == "/recover.jpg":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, request=request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kept, skipped = await filter_reachable_image_candidates(candidates, client=client)

    assert [item["original_media_url"] for item in kept] == [
        "https://cdn.example.org/good.jpg",
        "https://cdn.example.org/head-blocked.jpg",
        "https://cdn.example.org/recover.jpg",
        "https://cdn.example.org/news.mp4",
    ]
    assert skipped == 2
