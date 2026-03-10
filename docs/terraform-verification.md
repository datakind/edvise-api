# Verifying whether Terraform was used to deploy Edvise

Use this checklist in GCP to see if the live infrastructure matches the Terraform in this repo. If it does, Terraform was (or is) the deployment path. If names or types differ (e.g. Postgres instead of MySQL), something else was used.

## 1. Database: MySQL vs Postgres

**Terraform creates:** Cloud SQL **MySQL 8.0** instances. The app code (edvise-api and edvise-ui) is written for **MySQL** (e.g. `mysql+pymysql`, `DB_CONNECTION=mysql`, port 3306 in the service module).

**In GCP:**

- **Cloud Console:** SQL → list instances. For each instance, check **Database version**.
  - If you see **PostgreSQL**: that instance was **not** created by this Terraform (this Terraform only creates MySQL).
  - If you see **MySQL 8.0** and names below match, it could be from this Terraform.

- **gcloud:**
  ```bash
  gcloud sql instances list --project=YOUR_PROJECT_ID --format="table(name,databaseVersion,region)"
  ```
  `databaseVersion` should be `MYSQL_8_0` for Terraform-created instances.

## 2. Resource names Terraform would create

If Terraform was used, you should see these **exact** naming patterns (replace `{env}` with `dev`, `staging`, or `prod`).

| Resource type | Terraform name pattern | Where to look in GCP |
|--------------|------------------------|----------------------|
| Cloud SQL instance | `{env}-db-instance` | SQL → instances |
| Cloud Run services | `{env}-webapp`, `{env}-frontend`, `{env}-worker` | Cloud Run |
| Secret Manager secrets | `{env}-db-password`, `{env}-db-client-cert`, `{env}-db-client-key`, `{env}-db-server-ca`, `{env}-webapp-env-file`, `{env}-frontend-env-file`, `{env}-worker-env-file` | Security → Secret Manager |
| GCS bucket (static assets) | `{PROJECT_ID}-{env}-static` | Cloud Storage |
| Load balancer / URL map | `{env}-tf-cr-lb-1`, `{env}-tf-cr-url-map-1` | Network services → Load balancing |
| Global static IP | `{env}-tf-cr-lb-1-address` | VPC network → IP addresses |
| Backend bucket (static) | `{env}-tf-cr-static-build-1` | Load balancing → Backends |
| VPC network (in service project) | `{env}-vpc-network` | VPC network → Networks (if not using shared VPC only) |
| Subnet (in host project) | `{env}-vpc-subnetwork` | VPC network → Subnets |

If you see **different** names (e.g. no `-tf-cr-` in the LB, or instance names like `edvise-db` instead of `dev-db-instance`), that suggests a different deployment (manual or another IaC).

## 3. Terraform state buckets

Terraform stores state in GCS. The repo references:

- **Dev:** `sst-terraform-state-184227`
- **Prod:** `sst-terraform-state-749293`
- **Staging:** (check `terraform/environments/staging/main.tf` for bucket name)

**In GCP:** Cloud Storage → Buckets. Search for `sst-terraform-state` or the bucket names above.

- If these buckets **exist** and contain objects (e.g. `default.tfstate` or env-prefixed state), Terraform was at least **used** for some apply.
- If they’re **empty or missing**, either Terraform was never run or state is stored elsewhere.

## 4. Quick gcloud checks (replace PROJECT and REGION)

```bash
# List Cloud Run services (expect dev-webapp, dev-frontend, dev-worker for dev)
gcloud run services list --project=YOUR_PROJECT_ID --region=YOUR_REGION --format="table(SERVICE,REGION)"

# List Cloud SQL instances (expect dev-db-instance etc.; check databaseVersion)
gcloud sql instances list --project=YOUR_PROJECT_ID --format="table(name,databaseVersion,region)"

# List secrets (expect dev-db-password, dev-webapp-env-file, etc.)
gcloud secrets list --project=YOUR_PROJECT_ID --format="table(name)"

# List backends / URL maps (names contain -tf-cr- if from this Terraform)
gcloud compute backend-services list --project=YOUR_PROJECT_ID --format="table(name)"
gcloud compute url-maps list --project=YOUR_PROJECT_ID --format="table(name)"
```

## 5. How to interpret what you find

| What you see | Likely conclusion |
|--------------|--------------------|
| Cloud SQL is **PostgreSQL** | This Terraform was **not** used to create that instance (Terraform here is MySQL-only). Either manual/other IaC or a different project. |
| Cloud SQL is **MySQL** and names match (`*-db-instance`, etc.) | Consistent with this Terraform. Check LB and Cloud Run names to be sure. |
| Cloud Run services named `dev-webapp`, `dev-frontend`, `dev-worker` (and same for staging/prod) | Matches Terraform service module. |
| Load balancer / URL map names contain **`-tf-cr-`** (e.g. `dev-tf-cr-lb-1`) | Matches Terraform deployment module. |
| State buckets exist and have state files | Terraform has been run; compare state’s resource list to what’s in the console. |
| Names completely different (e.g. `edvise-prod-api`, no `-tf-cr-`) | Likely a different deployment path; this Terraform may be unused or used only for dev. |

## 6. If you have Terraform state access

From the repo, with correct backend and credentials:

```bash
cd terraform/environments/dev   # or staging / prod
terraform init
terraform state list
```

Then compare `terraform state list` output to resources in the project. If state lists resources that exist in GCP with matching IDs, that environment was deployed (and is managed) by this Terraform.

---

**Verification status:** Dev has been confirmed: state in GCS bucket (`sst-terraform-state-184227`), MySQL Cloud SQL (`dev-db-instance`), Cloud Run services and LB names match this Terraform. Use the checklist above for staging/prod or after major changes.
