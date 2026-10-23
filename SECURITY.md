# Security

Please report a vulnerability privately, through GitHub: on this repository's **Security** tab,
choose **Report a vulnerability**. Don't open a public issue for it. Fixes go into the latest
release only.

Foldocs reads a publicly shared folder and builds static files from it, so the parts most worth a
look are where it handles what the folder holds: converting documents (`foldocs/convert.py`), the
`design` document (`foldocs/design.py`), and the responses of a Nextcloud share
(`foldocs/sources/nextcloud.py`).
