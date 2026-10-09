# Lovable prompt — Aalora frontend on the Simmerca API

Paste the block below into Lovable (new project, or your existing Aalora project). Attach `openapi.yaml`
from this repo. Then set the four environment variables from the CDK stack outputs.

```
VITE_API_URL=<ApiUrl output>
VITE_AWS_REGION=us-west-2
VITE_USER_POOL_ID=<UserPoolId output>
VITE_USER_POOL_CLIENT_ID=<UserPoolClientId output>
```

---

## Prompt

Build the Aalora web app as a frontend ONLY. All data comes from the Simmerca REST API described in the
attached openapi.yaml at `import.meta.env.VITE_API_URL`. Do NOT use Supabase or any other backend, database
or edge functions for this app. Do not invent endpoints; if something is missing, show a TODO in the UI.

**Auth:** AWS Cognito via `aws-amplify` v6 (`Amplify.configure({ Auth: { Cognito: { userPoolId, userPoolClientId } } })`).
Use the Amplify Authenticator or custom forms (sign up with email, confirm code, sign in, forgot password).
For every `/admin/*` and `/b2b/*` call, send `Authorization: Bearer <idToken>` from `fetchAuthSession()`.
Read `cognito:groups` from the ID token: users in `admin` see the Admin app; users in `buyer` see the
Wholesale portal. Create one typed API client module (`src/lib/api.ts`) generated from the OpenAPI schemas;
all money fields are integer cents: format with `Intl.NumberFormat` using the `currency` field.

**Design:** quiet luxury, editorial. Generous whitespace, a serif display face for headings, a clean sans for
UI, warm ivory background, deep maroon accent, thin gold rules. Photography-first product pages. Mobile-first.

### 1. Storefront (public, `/`)
- Catalog grid from `GET /public/products`, filter chips by fabric (`?fabric=`).
- Product page `GET /public/products/{sku}`: image gallery, title, price, the `story` as a prominent
  "The making of this piece" section, `provenance_region` and `craft`, attribute chips.
- Availability badge: `in_stock` → "Ready to ship" (+ "Only N left" when quantity ≤ 2),
  `made_to_order` → "Made to order · ~{lead_time_days} days", `out_of_stock` → "Sold out" with a "Notify me" button (UI only).
- Keep the existing Shopify headless checkout for "Add to bag" (Storefront API) — orders flow back to
  the backend through the Shopify webhook. Do not show stock numbers beyond the badge.

### 2. Admin app (`/admin`, group `admin`)
- **Dashboard** (`GET /admin/dashboard`): tiles for products by status, low stock, reviews waiting,
  price approvals waiting, open alerts, quotes waiting, buyers waiting. Each tile links to its screen.
- **Products** (`GET /admin/products`): table with sku, image, title, status, on_hand, price, channels, made-to-order flag.
  "New product" form (`POST /admin/products`).
- **Product detail** (`GET /admin/products/{sku}`):
  - Edit form (`PATCH`). Price is editable only while status is draft; otherwise show "Propose price".
  - Stock panel: current on_hand, buttons "Received +", "Sold −", "Damaged −", "Set count". Each submit sends
    `POST /admin/products/{sku}/stock` with a fresh `crypto.randomUUID()` as `idempotency_key`.
  - Ledger timeline (`GET .../ledger`): time, reason, delta, source (whatsapp/shopify/etsy/admin/b2b), actor.
  - Images: upload with `POST .../images` → PUT the file to `upload_url` with the same Content-Type → refresh.
  - "Analyse with AI" (`POST .../intelligence`, 202) → toast "Queued; check the review queue".
  - Channels: one row per channel (aalora, shopify, etsy, meta) showing linked/not linked, last pushed qty,
    last error. "Link" (`POST .../listings {channel}`), "Unlink" (`DELETE`), "Re-sync" (`POST .../sync`).
  - Price: "Suggest" (`GET .../price-suggestion`) shows suggested, rule, floor and method; "Propose" (`POST .../price-proposals`).
- **Review queue** (`GET /admin/review-queue`): card per product with images and each proposed attribute
  as a dropdown pre-filled with the AI value and a confidence pill (red < 0.8). Fields in `needs_review_fields`
  are highlighted. "Approve" sends `POST .../attributes/approve {edits}` with only the changed fields.
- **Price approvals** (`GET /admin/price-proposals`): old → new, reason, approve/reject.
- **Suppliers** (`GET/POST /admin/suppliers`, `PATCH` active toggle): name, WhatsApp phone (E.164), language, region, craft.
  This screen is internal; supplier data never appears in storefront or wholesale screens.
- **Alerts** (`GET /admin/alerts`): OVERSELL, UNMAPPED_SKU etc. with resolve button.
- **Wholesale**: buyers (`GET /admin/buyers`, approve/reject), quotes (`GET /admin/quotes`, approve with optional
  per-line price override and message, reject), production orders (`GET /admin/production-orders`).

### 3. Wholesale portal (`/wholesale`, group `buyer`)
- After sign-up, a profile form (`PUT /b2b/me`). While status is `pending`, show "Your account is under review".
- Catalog (`GET /b2b/catalog`) with tier price table per product.
- Cart → "Request quote" (`POST /b2b/quotes`). My quotes list and detail (`GET /b2b/quotes`, `/b2b/quotes/{id}`).

**Errors:** API errors return `{ "error": "message" }`; show it in a toast. 403 → "You don't have access".
```
