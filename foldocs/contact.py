"""The site's contact links, from the links in the root folder's intro: the first email address,
for a theme's contact button, and links to the author's profiles elsewhere, by site. The links
come from the document's conversion (convert.Doc.links), so nothing here reads HTML."""

import re
from urllib.parse import urlparse

# Sites a link in the intro can be a profile on, by host (a subdomain counts too), and the shape
# of a profile's path there: a link to a video or a repository is no profile.
ONE = r"/[^/]+"  # one segment: x.com/ann
CODE = r"/[^/]+(/[^/]+)?"  # a person or a project: github.com/ann, github.com/ann/project
PROFILES = {
    "linkedin.com": ("LinkedIn", r"/(in|company)/[^/]+"),
    "bsky.app": ("Bluesky", r"/profile/[^/]+"),
    "x.com": ("X", ONE),
    "twitter.com": ("X", ONE),
    "instagram.com": ("Instagram", ONE),
    "threads.net": ("Threads", r"/@[^/]+"),
    "threads.com": ("Threads", r"/@[^/]+"),
    "facebook.com": ("Facebook", ONE),
    "github.com": ("GitHub", CODE),
    "gitlab.com": ("GitLab", CODE),
    "youtube.com": ("YouTube", r"/(@[^/]+|(channel|c|user)/[^/]+)"),
    "vimeo.com": ("Vimeo", ONE),
    "tiktok.com": ("TikTok", r"/@[^/]+"),
    "behance.net": ("Behance", ONE),
    "dribbble.com": ("Dribbble", ONE),
    "medium.com": ("Medium", r"/@[^/]+|"),  # medium.com/@ann, or ann.medium.com
    "substack.com": ("Substack", r""),  # ann.substack.com
    "orcid.org": ("ORCID", ONE),
    "scholar.google.com": ("Google Scholar", r"/citations"),
}
NOT_PROFILES = {"share", "sharer", "intent", "home", "watch", "search", "explore", "hashtag"}


def profile(href: str) -> tuple[str, str] | None:
    """(site, normalized address) for a link to a profile, else None. Mastodon is any server's
    https://server/@name."""
    url = urlparse(href)
    host, path = (url.hostname or "").removeprefix("www."), url.path.rstrip("/")
    if url.scheme not in ("http", "https") or path.strip("/").split("/")[0].lower() in NOT_PROFILES:
        return None
    for domain, (name, shape) in PROFILES.items():
        if host == domain or host.endswith("." + domain):
            if not path and host == domain:  # the site's home page: nobody's profile
                return None
            # Google Scholar tells its profiles apart by the user= in the query, not by the path.
            key = f"{host}{path}?{url.query}" if domain == "scholar.google.com" else f"{host}{path}"
            return (name, key.lower()) if re.fullmatch(shape, path) else None
    if url.scheme == "https" and re.fullmatch(r"/@[\w.]+", path):
        return "Mastodon", f"{host}{path}".lower()
    return None


def links(hrefs: list[str]) -> tuple[str | None, list[tuple[str, str]]]:
    """The first email address among the intro's links, and its links to profiles, once each."""
    email, profiles, seen = None, [], set()
    for href in hrefs:
        if href.lower().startswith("mailto:"):
            email = email or href[7:].split("?")[0]
        elif (found := profile(href)) and found[1] not in seen:
            seen.add(found[1])
            profiles.append((found[0], href))
    return email, profiles
