# Andrey Lebedev · Portfolio

Static six-page portfolio prototype prepared for AWS Amplify Hosting.

## Local preview

Open `index.html` in a browser. HTML pages navigate using relative links, and
CSS and images are stored in `assets/`. For local HTTP serving (optional):

```sh
python3 -m http.server 8000
```

The iOS app catalogue uses links to the public App Store. Apple app icons
are downloaded from Apple's official iTunes Lookup API **during Amplify builds**.
To see the fully built site with those icons locally, run:

```sh
mkdir -p dist
cp index.html career.html apps.html 3d-printing.html art.html behind-the-site.html dist/
cp -r assets dist/assets
python3 scripts/fetch_apple_icons.py dist
python3 -m http.server --directory dist 8000
```

## Deploy using Amplify Hosting

1. Create a GitHub repository and push this directory to the `main` branch.
2. In AWS Console, open **AWS Amplify → Create new app**.
3. Choose GitHub, authorise the AWS Amplify GitHub App, choose this repository and `main`.
4. Amplify reads the root `amplify.yml`, prepares `dist/`, downloads official
   app icons from Apple and deploys the static pages.
5. Open the temporary `*.amplifyapp.com` URL. Pushes to `main` trigger redeploys.

No Node dependencies or AWS credentials are needed in this repository.
The AI assistant and recruiter portal are currently scripted interface demos,
not live services. Please review all public profile content before launching.