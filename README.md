# circle.basil.one

Source for [Circle3](https://circle.basil.one), a Jekyll site and paper presenting three guiding moves for leading difficult change.

## Quality

**Quality is a continuous practice in service of human connection.**

We keep Circle's website and paper dependable, accessible, and easy to use so readers can engage with the work. Automation carries repetitive checks and brings evidence and practical remedies to human judgment. The practice creates capacity rather than maintenance overhead.

Our [quality stance and practice](.github/QUALITY.md) describes the quality domains, safeguards in operation, and direction of the practice.

## Development

1. Open in GitHub Codespaces (uses the provided dev container), or use Ruby 3.2+ with Bundler locally.
2. Install dependencies via terminal: `bundle install`
3. Run locally via terminal: `bundle exec jekyll serve`

The site will be available at http://localhost:4000.

## Site Publication

`Site: Publish` builds the production site with the repository's frozen Bundler
dependencies and publishes it to GitHub Pages after pushes to `main`. It can
also be dispatched manually from `main` for recovery. It is a publication
workflow, not an additional pull-request quality check.

### Pages cutover and rollback

The workflow does not change repository Pages settings. Before the cutover,
record the current Pages source branch and folder in **Settings → Pages**.
After this change is merged, switch **Build and deployment → Source** to
**GitHub Actions**, keeping the existing custom domain and HTTPS setting. Check
the merge-triggered `Site: Publish` run. If it started before the source switch
or did not deploy, run `Site: Publish` manually from the current `main` branch.
In **Actions**, open **Site: Publish**, choose **Run workflow**, select
`main`, and start the run.
Verify the deployed run's source SHA and check the live custom-domain site,
HTTPS, and paper download before considering the cutover complete.

To roll back the publication mechanism, restore the Pages source branch and
folder recorded before cutover, then verify the site and HTTPS. This returns to
the previous Pages builder and its existing dependency posture, so use it only
as a temporary recovery. To roll back site content while keeping Actions
publication, revert the affected change on `main`; do not dispatch an older
revision.

## Intellectual stewardship

We develop original work while recognizing that knowledge emerges through practice, relationships, and the contributions of others.

- **Generosity:** Recognize the people and work that have meaningfully influenced our own.
- **Integrity:** Represent the origins, authorship, evidence, and limitations of our work accurately, including the role of AI where appropriate.
- **Independence:** Develop our own synthesis and expression without misappropriating the intellectual identity or protected materials of others.
- **Reciprocity:** Respect the intellectual property of others as we would want our own respected.

Apply these principles proportionately when creating, adapting, reviewing, and publishing content. Honor the licensing boundaries of each site, distinguish original work from third-party materials, and avoid claiming rights we do not hold.

This project uses AI-assisted drafting, editing, and illustration. The author reviews and finalizes the published work. The paper includes its own AI disclosure.

## License

See the [licensing policy](LICENSE.md) for scope and terms. Original website software is MIT-licensed; designated original practice materials are CC BY-SA 4.0; other writing and site copy are all rights reserved unless otherwise noted. Paper licenses follow their own notices and publication terms. Third-party rights are preserved.
