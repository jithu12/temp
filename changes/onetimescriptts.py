from typing import Union
import logging

from dataviz_core.models import Status
from dataviz_core.core import DatavizCore
from dataviz_core.management_scripts.config import local_config
from dataviz_core.services.filtering import FilteringCriterion

LoggerType = Union[logging.Logger, logging.LoggerAdapter]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeleteActiveNamespaces:
    def __init__(self):
        print("\n" + "=" * 70)
        print("   🚀  KUBERNETES NAMESPACE DELETION SCRIPT")
        print("=" * 70)

        print("\n📦 Initializing DatavizCore...")
        self.core = DatavizCore(config=local_config)
        print("✅ DatavizCore initialized successfully")

    # ─────────────────────────────────────────────────────────────────────────
    # FETCH NAMESPACES (already from DB via repository)
    # ─────────────────────────────────────────────────────────────────────────
    def get_active_namespaces(self):
        print("\n" + "=" * 70)
        print("🔍 Fetching ACTIVE namespaces from DB...")

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

            print(f"\n✅ Total ACTIVE namespaces found: {len(ns_list)}")
            print(f"\n{'#':<5} {'Name':<45} {'External ID':<40} {'Status'}")
            print("─" * 105)

            for i, ns in enumerate(ns_list, 1):
                print(
                    f"{i:<5} {str(ns.name):<45} "
                    f"{str(ns.external_id):<40} "
                    f"{str(ns.status)}"
                )

            return ns_list

        except Exception as e:
            print(f"❌ Error fetching namespaces: {e}")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # DELETE NAMESPACE  (uses core kube service — handles DB update internally)
    # ─────────────────────────────────────────────────────────────────────────
    def delete_namespace(self, ns):
        print(f"\n   🗑️  Deleting: {ns.name}  |  external_id: {ns.external_id}")

        try:
            self.core.kube._delete_temp_namespace(ns)
            print(f"   ✅ Successfully deleted: {ns.name}")
            return True

        except Exception as e:
            print(f"   ❌ Failed to delete: {ns.name}")
            print(f"      Error: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────────────────────────────────────
    def run(self):

        # ── 1. Fetch ──────────────────────────────────────────────────────────
        ns_list = self.get_active_namespaces()

        if not ns_list:
            print("\n⚠️  No ACTIVE namespaces found. Exiting.")
            return

        # ── 2. Confirm ────────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        confirm = input(
            f"\n❓ Do you want to DELETE all {len(ns_list)} namespace(s)? (yes/no): "
        ).strip().lower()

        if confirm != "yes":
            print("❌ Deletion cancelled by user. Exiting.")
            return

        # ── 3. Delete loop ────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("🗑️  Starting deletion...\n")

        success_list = []
        failed_list  = []

        for idx, ns in enumerate(ns_list, 1):
            print(f"[{idx}/{len(ns_list)}]")
            print("─" * 70)

            if self.delete_namespace(ns):
                success_list.append(ns.name)
            else:
                failed_list.append(ns.name)

        # ── 4. Summary ────────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("📊  FINAL SUMMARY")
        print("=" * 70)
        print(f"  ✅ Successfully Deleted : {len(success_list)}")
        print(f"  ❌ Failed               : {len(failed_list)}")

        if success_list:
            print("\n✅ Deleted:")
            for name in success_list:
                print(f"   - {name}")

        if failed_list:
            print("\n❌ Failed:")
            for name in failed_list:
                print(f"   - {name}")

        print("\n🏁 Script complete!")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    obj = DeleteActiveNamespaces()
    obj.run()
