# Configuration Directory

This directory contains the configuration files for Raspilapse.

## Files

### `config.example.yml`
**Template/Example configuration file**
- This is the default configuration template tracked in git
- Contains all available settings with documentation and examples
- Safe to reference for documentation purposes
- **Do not modify this file directly** - copy it to `config.yml` first

### `config.yml`
**Your personal configuration file**
- **Not tracked by git** - safe to customize with your personal settings
- You create it by copying `config.example.yml`; nothing does it for you.
  `./scripts/install.sh --check` will say so if you have not
- This is the file that Raspilapse actually uses
- Customize this with your own:
  - Camera settings (resolution, exposure, etc.)
  - Output paths
  - Weather API endpoints
  - Overlay content
  - Project name and camera name

## Quick Start

### First-Time Setup

When you first clone/install Raspilapse:

```bash
# ./scripts/install.sh --check will tell you if you have not done this:
cp config/config.example.yml config/config.yml

# Or do it manually:
cd config/
cp config.example.yml config.yml
```

### Customize Your Config

Edit `config.yml` with your settings:

```bash
nano config/config.yml
```

Key settings to customize:
1. **Output directory** (`output.directory`) - Where images are saved
2. **Project name** (`output.project_name`) - Used in filenames
3. **Camera name** (`overlay.camera_name`) - Shown in overlays
4. **Resolution** (`camera.resolution`) - Image size
5. **Weather endpoint** (`weather.endpoint`) - If using weather integration

### After Editing Config

If the service is running, restart it to apply changes:

```bash
sudo systemctl restart raspilapse
```

## Configuration Management

### Updating Raspilapse

When you pull updates from git:

```bash
git pull origin main
```

Your personal `config.yml` will **not be overwritten** because it's in `.gitignore`.

If there are new configuration options in `config.example.yml`:
1. Check the diff to see what's new:
   ```bash
   diff config/config.yml config/config.example.yml
   ```
2. Manually add new sections from `config.example.yml` to your `config.yml`

### Sharing Configs

If you want to share your configuration (without personal details):

```bash
# Somewhere outside config/, which is not gitignored for arbitrary names
cp config/config.yml ~/my-setup.yml

# Strip anything private: video_upload.api_key, weather.endpoint, the
# pi-overlay-data paths, and your location if you would rather not share it
nano ~/my-setup.yml
```

Copying it to `config/my-setup.yml` instead would stage a file this section is
telling you to sanitise — only `config.yml` and `config/*.backup*` are
ignored.

### Reset to Defaults

To reset your configuration to defaults:

```bash
# Backup your current config
cp config/config.yml config/config.yml.backup

# Copy example config
cp config/config.example.yml config/config.yml

# Restart service
sudo systemctl restart raspilapse
```

## Configuration Reference

`config.example.yml` is the reference: every setting is explained inline, and
`tests/test_config_example.py` fails if it drifts from what the code reads.

For the areas with more to say than fits in a comment:
- **docs/OVERLAY.md** - Overlay system configuration
- **docs/WEATHER.md** - Weather integration setup
- **docs/EXPOSURE.md** - Exposure control settings

## Troubleshooting

### "Config file not found" Error

If you see this error:
```
FileNotFoundError: config/config.yml not found
```

Solution:
```bash
cp config/config.example.yml config/config.yml
```

### "Invalid YAML syntax" Error

If you see YAML parsing errors:
1. Check for proper indentation (use spaces, not tabs)
2. Validate your YAML syntax: https://www.yamllint.com/
3. Compare with `config.example.yml` for proper structure

### "Permission denied" Error

If you can't write to output directory:
```bash
sudo chown -R $USER:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images
```

## Version Control Best Practices

### What's Tracked in Git
✅ `config.example.yml` - Template configuration
✅ `config/README.md` - This documentation

### What's NOT Tracked
❌ `config.yml` - Your personal configuration
❌ `config/*.backup*` - Backup files

Note that the ignore rules cover exactly those two patterns. Any other `.yml`
you leave in `config/` **will** be committed.

This ensures:
- You can safely customize `config.yml` without git conflicts
- You can pull updates without losing your settings
- Example config stays up-to-date in the repository
