# Configuration

Three files, each with a different job.

| File | What it is |
|------|-----------|
| `config/config.example.yml` | A short starter file, tracked in git. Copy it, don't edit it. |
| `config/config.yml` | Yours. Gitignored, because it holds API keys. |
| [`docs/CONFIG-REFERENCE.yml`](../docs/CONFIG-REFERENCE.yml) | Every setting there is, annotated. Reference, not a template. |

Anything your `config.yml` does not mention has a default in
`raspilapse/config.py`, so it only needs to contain what you want to change.
The example used to be 681 lines and was the schema as well as the starting
point; that is what made a first look at this project harder than it needed to
be.

## First-time setup

```bash
cp config/config.example.yml config/config.yml
nano config/config.yml
```

`./scripts/install.sh --check` will tell you if you have not done this.

Worth setting straight away:

1. `output.directory` — where images land; must exist and be writable
2. `output.project_name` — appears in every filename
3. `location` — recorded with each frame
4. `camera.resolution` — the code defaults to 1920x1080; the example ships 4K,
   which is where the disk figures in the docs come from

Restart the service to apply changes:

```bash
sudo systemctl restart raspilapse
```

## Adding a setting

Find it in `docs/CONFIG-REFERENCE.yml`, copy that block into your `config.yml`,
and edit it. There is no need to copy the surrounding sections — the merge is
per-key, so setting `output.quality` alone leaves every other `output` setting
at its default.

## Updating

```bash
git pull
```

Your `config.yml` is never touched. New settings appear in
`docs/CONFIG-REFERENCE.yml` with their defaults already applied, so a pull
cannot change your camera's behaviour by adding one.

`tests/test_config_example.py` keeps the three files honest. The two checks
that matter most:

- every setting the reference documents is one the code actually reads
- every setting the code *requires* has a default, so a short config still works

## Sharing a config

```bash
# Somewhere outside config/, which is not gitignored for arbitrary names
cp config/config.yml ~/my-setup.yml

# Strip anything private: video_upload.api_key, weather.endpoint, the
# pi-overlay-data paths, and your location if you would rather not share it
nano ~/my-setup.yml
```

Copying it to `config/my-setup.yml` instead would stage a file this section is
telling you to sanitise — only `config.yml` and `config/*.backup*` are ignored.
Any other `.yml` you leave in `config/` **will** be committed.

## Reset to defaults

```bash
cp config/config.yml config/config.yml.backup
cp config/config.example.yml config/config.yml
sudo systemctl restart raspilapse
```

## Troubleshooting

**`FileNotFoundError: Configuration file not found: config/config.yml`** — you
have not copied the example yet. See "First-time setup" above.

**Invalid YAML** — check the indentation is spaces rather than tabs, and paste
the file into <https://www.yamllint.com/>.

**Permission denied writing images**

```bash
sudo chown -R $USER:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images
```

For the areas with more to say than fits in a comment:
[OVERLAY.md](../docs/OVERLAY.md), [WEATHER.md](../docs/WEATHER.md),
[EXPOSURE.md](../docs/EXPOSURE.md).
