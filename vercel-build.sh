#!/bin/sh
# Vercel build for the landing page. ONLY the files listed here are published
# (the output folder is `public/`), so the private app source in this repo is
# never served. Landing-page absolute links that point at the old GitHub
# Pages site are rewritten to this deployment's own production domain.
set -e
rm -rf public
mkdir public
cp index.html public/
for f in qa-logo.png qa-logo-icon.png og-image-v2.png og-image.png app.png app_new_icon_1024.png; do
  [ -f "$f" ] && cp "$f" public/ || true
done
SITE="https://${VERCEL_PROJECT_PRODUCTION_URL:-${VERCEL_URL:-}}"
if [ "$SITE" != "https://" ]; then
  sed -i "s#https://ahmedsayedrepo.github.io/QA-Studio/#${SITE}/#g; s#https://ahmedsayedrepo.github.io/QA-Studio#${SITE}#g" public/index.html
fi
echo "Landing page built for ${SITE}:"
ls -la public
