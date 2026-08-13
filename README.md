# PSKC Links

The official link hub for the [Psychedelic Society of Kansas City](https://psychedelickc.org/), built from [LittleLink](https://littlelink.io/) v3.11.0 and hosted on GitHub Pages.

## Local preview

No build or dependency installation is required:

```sh
python3 -m http.server 8000 --directory site
```

Then open <http://localhost:8000>.

## Edit the page

- Links and page copy: `site/index.html`
- PSKC-specific presentation: `site/css/custom.css`
- LittleLink upstream styles: `site/css/style.css` and `site/css/brands.css`
- Profile and social image: `site/images/avatar.png` and `site/images/avatar@2x.png`
- Future Nostr and Matrix links: find `Future federated/community links` in `site/index.html`, replace the placeholder URLs, and uncomment the buttons

Pushes to `main` are deployed by `.github/workflows/pages.yml`. The initial public URL is:

<https://heartlandtranspersonalalliance.github.io/links/>

This is intentionally a static site. It has no admin panel, database, analytics, cookies, or runtime server to maintain. Changes are made in this repository and published by the workflow.

## Enable GitHub Pages

In the repository, open **Settings → Pages** and set **Source** to **GitHub Actions**. A push to `main` (or a manual run from the Actions tab) will publish the site.

## Move to `links.psychedelickc.org`

Do these in order after the default Pages URL is working:

1. In the Heartland Transpersonal Alliance organization settings, open **Pages** and [verify `psychedelickc.org`](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/verifying-your-custom-domain-for-github-pages-site). Keep GitHub's verification TXT record in DNS.
2. In this repository, open **Settings → Pages**, enter `links.psychedelickc.org` under **Custom domain**, and save it.
3. At the DNS provider for `psychedelickc.org`, add a CNAME record named `links` pointing to `heartlandtranspersonalalliance.github.io` (without `/links`). Do not use a wildcard record.
4. After DNS and the certificate are ready, enable **Enforce HTTPS** in the repository's Pages settings.
5. Replace the three default GitHub Pages URLs in `site/index.html` (`canonical`, `og:url`, and `og:image`) with `https://links.psychedelickc.org/` URLs.

GitHub Actions deployments ignore a repository `CNAME` file, so the custom domain belongs in GitHub's Pages settings and in DNS.

## License and upstream

LittleLink is available under the MIT license; see `LICENSE.md`. The upstream styles, fonts, and icons in `site/` are based on LittleLink v3.11.0. PSKC-specific copy and presentation are maintained in this repository.
