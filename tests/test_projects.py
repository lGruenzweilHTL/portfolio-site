"""Project pages: README sanitising (kit classes) and the page's tidy-up."""
from backend.projects import GithubSource, sanitize_readme
from backend.routes.projects import _tidy_readme

SRC = GithubSource("o", "r")


def test_readme_gets_kit_classes():
    out = sanitize_readme(
        '<table><tr><td>a</td></tr></table><hr>'
        '<p><a href="shot.png"><img src="shot.png" alt="Shot"></a></p>'
        '<p><img src="https://img.shields.io/badge/x-y-blue"></p>',
        SRC,
    )
    assert '<div class="table-wrap"><table class="table">' in out and "</table></div>" in out
    assert '<hr class="rule" data-chalk="rule">' in out
    assert '<span class="rd-pic" data-chalk="frame"><img ' in out
    assert 'src="https://raw.githubusercontent.com/o/r/HEAD/shot.png"' in out
    assert 'class="rd-badge"' in out and out.count("rd-pic") == 1


def test_badge_behind_camo_is_recognised():
    # camo hex-encodes the original URL: https://img.shields.io/badge
    camo = "https://camo.githubusercontent.com/abc/" + "https://img.shields.io/badge".encode().hex()
    assert 'class="rd-badge"' in sanitize_readme(f'<img src="{camo}">', SRC)


def test_github_alert_stays_a_quote():
    out = sanitize_readme(
        '<div class="markdown-alert markdown-alert-tip"><p class="markdown-alert-title"><svg><path/></svg>Tip</p>'
        "<p>Text</p></div><div><p>plain</p></div>",
        SRC,
    )
    assert out == '<blockquote class="rd-alert"><p class="rd-alert-title">Tip</p><p>Text</p></blockquote><p>plain</p>'


def test_tidy_drops_repeated_title_and_lifts_headings():
    readme = '<h3>Marshal</h3><span id="marshal"></span>\n<p>Intro</p><h4>Why</h4><h5>Sub</h5>'
    assert _tidy_readme(readme, {"name": "Marshal"}, None) == "<p>Intro</p><h3>Why</h3><h4>Sub</h4>"
    # a README title that says something else stays
    other = "<h3>Lego Spike Serial commands</h3><h4>Usage</h4>"
    assert _tidy_readme(other, {"name": "RoboDucks — RescueMaze"}, None) == other


def test_tidy_drops_readme_copy_of_the_screenshot():
    readme = (
        '<p><a href="x"><span class="rd-pic" data-chalk="frame"><img src="https://raw.githubusercontent.com/o/r/HEAD/docs/images/dashboard.png" alt="d"></span></a></p>\n'
        "<p>Keep</p>"
    )
    assert _tidy_readme(readme, {"name": "X"}, {"readme_dup": "/docs/images/dashboard.png"}) == "<p>Keep</p>"


def test_output_is_balanced_whatever_the_readme_does():
    # an unclosed table still gets its wrapper closed
    assert sanitize_readme("<table><tr><td>a", SRC) == (
        '<div class="table-wrap"><table class="table"><tr><td>a</td></tr></table></div>'
    )
    # stray end tags can't close the page's own elements
    assert sanitize_readme("<p>x</p></div></table></section></blockquote>", SRC) == "<p>x</p>"
    # an end tag closes what is still open inside it
    assert sanitize_readme("<ul><li>a<li>b</ul><p>c", SRC) == "<ul><li>a</li><li>b</li></ul><p>c</p>"
    # an alert left open is closed too
    out = sanitize_readme('<div class="markdown-alert"><p>t', SRC)
    assert out == '<blockquote class="rd-alert"><p>t</p></blockquote>'


def test_framed_picture_inside_a_link_and_a_table():
    out = sanitize_readme(
        '<table><tr><td><a href="https://x.dev"><img src="a.png"></a></td></tr></table>',
        SRC,
    )
    assert (
        '<td><a href="https://x.dev" target="_blank" rel="noopener"><span class="rd-pic" data-chalk="frame">'
        '<img src="https://raw.githubusercontent.com/o/r/HEAD/a.png" loading="lazy" decoding="async"></span></a></td>'
    ) in out
    assert out.endswith("</table></div>")


def test_github_actions_badge_is_a_badge():
    out = sanitize_readme('<img src="https://github.com/o/r/actions/workflows/ci.yml/badge.svg">', SRC)
    assert 'class="rd-badge"' in out and "rd-pic" not in out
