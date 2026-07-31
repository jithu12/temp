from typing import Union
import os
import logging
import psycopg2
import psycopg2.extras

from dataviz_core.models import Status
from dataviz_core.core import DatavizCore
from dataviz_core.management_scripts.config import local_config
from dataviz_core.services.filtering import FilteringCriterion
from dataviz_core.config.const import KUBE_CLIENT, COMMON_NAMESPACE_PREFIX

LoggerType = Union[logging.Logger, logging.LoggerAdapter]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_TABLE = "public.api_temp_kube_namespace"


class DeleteActiveNamespaces:
    def __init__(self):
        print("\n" + "=" * 70)
        print("   🚀  KUBERNETES NAMESPACE DELETION SCRIPT")
        print("=" * 70)

        # ── DatavizCore ──────────────────────────────────────────────────────
        print("\n📦 Initializing DatavizCore...")
        self.core = DatavizCore(config=local_config)
        self.cluster_name = KUBE_CLIENT
        print(f"✅ DatavizCore ready  |  Cluster: {self.cluster_name}")

        # ── Database ─────────────────────────────────────────────────────────
        self.db_conn = self._connect_to_db()

    # ─────────────────────────────────────────────────────────────────────────
    # DB CONNECTION
    # ─────────────────────────────────────────────────────────────────────────
    def _connect_to_db(self):
        print("\n🔌 Connecting to Database...")

        db_uri = (
            os.environ.get("DATABASE_URL")
            or os.environ.get("DB_URI")
            or os.environ.get("POSTGRES_URI")
        )

        if not db_uri:
            raise EnvironmentError(
                "❌ No DB URI found!\n"
                "   Please set one of: DATABASE_URL | DB_URI | POSTGRES_URI"
            )

        # Mask URI for safe printing
        masked = db_uri[:20] + "****" + db_uri[-10:] if len(db_uri) > 30 else "****"
        print(f"   URI (masked) : {masked}")

        try:
            conn = psycopg2.connect(db_uri)
            conn.autocommit = False
            print("✅ Database connected successfully")
            return conn
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # FETCH ACTIVE NAMESPACES FROM KUBERNETES
    # ─────────────────────────────────────────────────────────────────────────
    def get_active_namespaces_from_kube(self):
        print("\n" + "=" * 70)
        print("🔍 Fetching ACTIVE namespaces from Kubernetes...")

        filters = [
            FilteringCriterion(
                "status",
                [Status.ACTIVE],
                op="in",
            )
        ]

        try:
            ns_list = self.core.kube.repositories.temp_kube_namespace.list(
                filters=filters
            )

            print(f"\n✅ Total ACTIVE namespaces found in Kubernetes: {len(ns_list)}")
            print(f"\n{'#':<5} {'Full Name':<45} {'External ID':<40} {'Status'}")
            print("─" * 105)

            for i, ns in enumerate(ns_list, 1):
                full_name = COMMON_NAMESPACE_PREFIX + ns.suffix
                print(
                    f"{i:<5} {full_name:<45} "
                    f"{str(ns.external_id):<40} "
                    f"{str(ns.status)}"
                )

            return ns_list

        except Exception as e:
            print(f"❌ Error fetching namespaces from Kube: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # FETCH ACTIVE NAMESPACES FROM DB
    # ─────────────────────────────────────────────────────────────────────────
    def get_active_namespaces_from_db(self):
        print("\n" + "=" * 70)
        print(f"🗄️  Querying DB table: {DB_TABLE}")

        try:
            cur = self.db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(f"""
                SELECT
                    status,
                    name,
                    external_id,
                    kube_cluster_id_kube_namespace_id
                FROM {DB_TABLE}
                WHERE status = 'ACTIVE'
            """)
            rows = cur.fetchall()
            cur.close()

            # Key by external_id for O(1) lookup
            db_map = {str(row["external_id"]): dict(row) for row in rows}

            print(f"\n✅ Total ACTIVE namespaces found in DB: {len(db_map)}")
            print(f"\n{'#':<5} {'Name':<45} {'External ID':<40} {'Status'}")
            print("─" * 105)

            for i, (ext_id, row) in enumerate(db_map.items(), 1):
                print(
                    f"{i:<5} {row['name']:<45} "
                    f"{ext_id:<40} "
                    f"{row['status']}"
                )

            return db_map

        except Exception as e:
            print(f"❌ DB query failed: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # MATCH KUBE vs DB
    # ─────────────────────────────────────────────────────────────────────────
    def match_namespaces(self, kube_ns_list, db_map):
        print("\n" + "=" * 70)
        print("🔗 Matching Kubernetes namespaces with DB records...")

        matched    = []   # Found in both Kube and DB
        not_in_db  = []   # In Kube but NOT in DB

        for ns in kube_ns_list:
            if str(ns.external_id) in db_map:
                matched.append(ns)
            else:
                not_in_db.append(ns)

        print(f"\n  ✅ Matched  (in both Kube + DB) : {len(matched)}")
        print(f"  ⚠️  Not in DB (Kube only)        : {len(not_in_db)}")

        if not_in_db:
            print("\n⚠️  These namespaces exist in Kube but have NO DB record:")
            for ns in not_in_db:
                print(
                    f"   - {COMMON_NAMESPACE_PREFIX + ns.suffix:<45} "
                    f"external_id: {ns.external_id}"
                )

        return matched, not_in_db

    # ─────────────────────────────────────────────────────────────────────────
    # DELETE NAMESPACE FROM KUBERNETES
    # ─────────────────────────────────────────────────────────────────────────
    def delete_namespace_from_kube(self, namespace):
        ns_name = COMMON_NAMESPACE_PREFIX + namespace.suffix
        print(f"   🗑️  Requesting Kube deletion: {ns_name}")

        try:
            # ── Option A: via kube_rp_client (direct K8s API) ────────────────
            self.core.kube_rp_client.delete_namespace(
                ns_name,
                self.cluster_name
            )
            # ── Option B: via core.kube (use if Option A doesn't exist) ──────
            # self.core.kube.request_stack_deletion(namespace)

            print(f"   ✅ Kube deletion successful: {ns_name}")
            return True

        except Exception as e:
            print(f"   ❌ Kube deletion FAILED: {ns_name}")
            print(f"      Error: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # UPDATE STATUS TO DELETED IN DB
    # ─────────────────────────────────────────────────────────────────────────
    def update_db_status_deleted(self, external_id):
        print(f"   📝 Updating DB → status='DELETED'  external_id={external_id}")

        try:
            cur = self.db_conn.cursor()
            cur.execute(
                f"UPDATE {DB_TABLE} SET status = %s WHERE external_id = %s",
                ("DELETED", str(external_id)),
            )
            affected = cur.rowcount
            self.db_conn.commit()
            cur.close()

            if affected > 0:
                print(f"   ✅ DB updated: {affected} row(s) set to DELETED")
            else:
                print(f"   ⚠️  No rows updated — external_id not found: {external_id}")

        except Exception as e:
            self.db_conn.rollback()
            print(f"   ❌ DB update FAILED for external_id={external_id}: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────────────────────────────────────
    def run(self):

        # ── 1. Fetch namespaces ───────────────────────────────────────────────
        kube_ns_list = self.get_active_namespaces_from_kube()
        if not kube_ns_list:
            print("\n⚠️  No ACTIVE namespaces found in Kubernetes. Exiting.")
            return

        # ── 2. Fetch DB records ───────────────────────────────────────────────
        db_map = self.get_active_namespaces_from_db()

        # ── 3. Match ──────────────────────────────────────────────────────────
        matched, not_in_db = self.match_namespaces(kube_ns_list, db_map)

        if not matched:
            print("\n⚠️  No matching namespaces to delete. Exiting.")
            return

        # ── 4. Confirm deletion ───────────────────────────────────────────────
        print("\n" + "=" * 70)
        print(f"⚠️   DELETION CONFIRMATION  —  {len(matched)} namespace(s) will be deleted")
        print("=" * 70)
        for ns in matched:
            print(
                f"   🗑️  {COMMON_NAMESPACE_PREFIX + ns.suffix:<45} "
                f"external_id: {ns.external_id}"
            )

        confirm = input(
            f"\n❓ Do you want to DELETE these {len(matched)} namespace(s)? (yes/no): "
        ).strip().lower()

        if confirm != "yes":
            print("❌ Deletion cancelled by user. Exiting.")
            return

        # ── 5. Delete loop ────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("🗑️  Starting deletion process...\n")

        success_list = []
        failed_list  = []

        for idx, ns in enumerate(matched, 1):
            ns_name = COMMON_NAMESPACE_PREFIX + ns.suffix
            print(f"[{idx}/{len(matched)}]  {ns_name}")
            print("─" * 70)

            # Step A: Delete from Kubernetes
            kube_deleted = self.delete_namespace_from_kube(ns)

            if kube_deleted:
                # Step B: Update DB only on success
                try:
                    self.update_db_status_deleted(ns.external_id)
                    success_list.append(ns_name)
                    print(f"   ✅ DONE: Deleted + DB updated for {ns_name}")
                except Exception as db_err:
                    reason = f"Kube deleted BUT DB update failed: {db_err}"
                    failed_list.append((ns_name, reason))
                    print(f"   ⚠️  {reason}")
            else:
                reason = "Kubernetes deletion failed — DB NOT updated"
                failed_list.append((ns_name, reason))
                print(f"   ❌ SKIPPED DB update: {reason}")

            print()

        # ── 6. Final Summary ──────────────────────────────────────────────────
        print("=" * 70)
        print("📊  FINAL SUMMARY")
        print("=" * 70)
        print(f"  ✅ Deleted + DB Updated  : {len(success_list)}")
        print(f"  ❌ Failed                : {len(failed_list)}")
        print(f"  ⚠️  Skipped (not in DB)  : {len(not_in_db)}")

        if success_list:
            print("\n✅ Successfully deleted:")
            for name in success_list:
                print(f"   - {name}")

        if failed_list:
            print("\n❌ Failed:")
            for name, reason in failed_list:
                print(f"   - {name}")
                print(f"     Reason: {reason}")

        if not_in_db:
            print("\n⚠️  Namespaces NOT found in DB (not deleted):")
            for ns in not_in_db:
                print(
                    f"   - {COMMON_NAMESPACE_PREFIX + ns.suffix:<45} "
                    f"external_id: {ns.external_id}"
                )

        self.db_conn.close()
        print("\n✅ Database connection closed.")
        print("🏁 Script complete!")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    obj = DeleteActiveNamespaces()
    obj.run()
