from __future__ import annotations

import hashlib
import importlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
SCRIPT_IMPLEMENTATION_HASHES = {
    ("runtime", "check_notebook_ai_dev_status.py"): "d8a7545bf1c91204df5a8ac276adaa3bb6aa7c038ffd1ead6615edf3a7da3b9b",
    ("runtime", "run_chaptered_import_job_worker.py"): "3a888e205bdf53ea431ae8ee52b3bd740f47a737064445fce444b5eb9df12264",
    ("runtime", "start_notebook_ai_dev.bat"): "2a07907bcd2fe857f107045ebef68b1a16f2cbcb8a971b1068d47615758cae64",
    ("importing", "import_book_ocr_layout_first.py"): "91d1e02d51c00bab33f211b39ab550a7f6d754bd8b14811413db25eca0fa6117",
    ("importing", "plan_chapter_ocr_first_promote.py"): "71549adcc39cce306dd7246787d854087907cda9c3daa9c2d68d1ff7d255067a",
    ("importing", "plan_ocr_layout_batch.py"): "dbc82931769c74592bc41f9b89a3029a7f5a33a6b2ea2527bffb28674863ab68",
    ("importing", "promote_ocr_first_candidates.py"): "89437e14c8a39f8f8dbe73369d3072c21ed300c0de80213aed9410e00f480d13",
    ("importing", "repair_book_layout_bboxes.py"): "4f48b3e031cff550aac188aaa6b2fce8730c28d0b92159ea381a92366ddfd7f8",
    ("index", "build_retrieval_fts.py"): "3dc2c3f5e3356678a49090952a3434e07ac45e07ac028019258494c66b9596db",
    ("index", "build_vector_store.py"): "7904025a9410440145a6e0c6f3215e054b49d5e1d800ccfebcd6739ded3a76be",
    ("index", "inspect_retrieval_fragments.py"): "0d3788843aceb4269817bce631df7c83b6e5b1bb0a756766e1e22241461495e0",
    ("index", "search_retrieval_fts.py"): "89223ae20d834902396cc2ee123af277e6716ca4a15ec9fe2456aaf0316f1d43",
    ("index", "status_retrieval_fts.py"): "908c2c277d1148c1f7fa93f3c33274c7ef0f3608c8717677068cefdd1b78f381",
    ("index", "sync_vector_store.py"): "b3e2974037c921784d9e4a8c5e2f1163ea4ce2218e08fbba5d9050724163ac15",
    ("maintenance", "audit_chunk_page_metadata.py"): "7ec35c0662534c8402ab9bca2e1a817375c22ad6ab30f80ddc0493a7a89882fb",
    ("maintenance", "diagnose_chapter_pdf_quality.py"): "cf9c7e9157d5e43439c750ee09405e100562a7228f0b16c5f038f042fb20b827",
    ("maintenance", "diagnose_pdf_page_count.py"): "a0b7e94c50d8ebc43d137c9fcec381d34224951aa41eed9f1999d8c306e87a99",
    ("maintenance", "phase110k_q0_mechanism_draft_write_plan.py"): "591fcc2a49ffd030d0995d11abec6f835d89945966fb67522a4688c50494624b",
    ("maintenance", "phase110k_q0_validate_mechanism_draft_json.py"): "9c299e6871d9b71fbaa2c2f6826bff0e1496e8caf278651dd4704e3bacdeb9d0",
    ("maintenance", "phase110k_q2_export_chatgpt_prompt_from_package.py"): "38938959a42bddaf1e34bb22e94a36f79c5fb5f3bc4615adbd6f4ba5c2001718",
    ("maintenance", "phase110k_q4_mechanism_draft_review_cli.py"): "431f56b3e387184d2853eb570e5ddea4aa498b6d6605bc5cde36ae50d03eac04",
    ("maintenance", "phase110k_q5_mechanism_draft_review_bundle_export.py"): "eff3c690763046c7cdd155e84c71703dceda727b4668b7fa47d6df0f27e5a49b",
    ("maintenance", "phase110k_r2_seed_espcn_frontend_acceptance_notes.py"): "ec23d8209133cb4162d12b45e1b0e0984002800cf24d12a45045a7728f3e7b14",
    ("maintenance", "r3_phase6d_backup_research_memory_db.py"): "b7a520037c68ad0786560a56b692ce5d19909ec56171dffaa24056f64a1c206b",
    ("migrations", "migrate_book_chapter_schema.py"): "fd1827456ba7b2d4d71ccc6c177a8714a693aae073ec3df116850e70152793d6",
    ("migrations", "phase110_r3_fix3_object_candidate_schema_migration.py"): "b0267e98231ed67e2236de2f17bb900443b7bedf5793834a8518f4e1a14ccdf3",
    ("migrations", "phase110k_enable_inspiration_mechanism_storage.py"): "1204e8d6bd87d1a9599b627671275bfabe670a1fe544ed5216a773cc303fccc6",
    ("migrations", "prepare_layout_schema.py"): "b9c931ecf443f5d45060ac9f1405e06695b306c4035273a8f0373f89fdce0724",
    ("migrations", "r3_note_correction_review_migration.py"): "ea2f94af0ef81367d5d2c0501b1970a329b2a3e9688d77ce3d8f4364d5afb093",
    ("migrations", "r3_phase7c_note_classification_review_migration.py"): "d93646e81eaa026b46c0b7c272b04401f77e7da57c4a38c50ea10a43f0df000b",
    ("zotero", "package_zotero_inspiration_plugin.py"): "1459b36ae3f8baa53d94d404e640b7acfeac6270d9fe14da1e0691e0afad28d0",
    ("zotero", "phase110_r3a_sync_zotero_notes_for_unit.py"): "59f4028910762fa606cdeb7f2b5f6b38e5116aaccd751fdaa8352989bfb363fa",
    ("zotero", "phase110k_p_b_alignment_writeback_plan.py"): "66995378202a38332e757c901791c7f08957432b1ba807a5b71d6c0a770702af",
    ("zotero", "phase110k_p_c_import_time_alignment_batch_dry_run.py"): "3e286925ae0aaf420f7439f7ba0addfae45ecb2720bc549ac21f5d9a349b7b8f",
    ("zotero", "phase110k_p_d_import_alignment_hook_dry_run.py"): "7b8a815b4eb5c6a79e3cd83cbfeb03e776d289d241c91589087fde2ccb7cb9a2",
    ("zotero", "phase110k_p_f_batch_alignment_writeback_apply.py"): "57c7d333ba6c29fcbe052bf5253a134e28e828ae2a0d5abed59bba863d68237e",
    ("zotero", "phase110k_p_inspiration_match_readiness_dry_run.py"): "41d9bedab92eee260bf066e4a9f2c5edaf1c065645322127f41aa3eaf1b41a90",
}


def test_all_legacy_entries_and_classified_implementations_exist() -> None:
    assert len(SCRIPT_IMPLEMENTATION_HASHES) == 37
    for category, filename in SCRIPT_IMPLEMENTATION_HASHES:
        assert (SCRIPTS_ROOT / filename).is_file()
        assert (SCRIPTS_ROOT / category / filename).is_file()
        assert (SCRIPTS_ROOT / category / "__init__.py").is_file()


def test_classified_implementation_hashes_are_stable() -> None:
    for (category, filename), expected_hash in SCRIPT_IMPLEMENTATION_HASHES.items():
        payload = (SCRIPTS_ROOT / category / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash


def test_python_legacy_entries_preserve_import_surfaces() -> None:
    for category, filename in SCRIPT_IMPLEMENTATION_HASHES:
        if not filename.endswith(".py"):
            continue
        stem = filename.removesuffix(".py")
        legacy = importlib.import_module(f"scripts.{stem}")
        implementation = importlib.import_module(f"scripts.{category}.{stem}")

        legacy_public = {name for name in vars(legacy) if not name.startswith("_")}
        implementation_public = {name for name in vars(implementation) if not name.startswith("_")}
        assert legacy_public == implementation_public
        if hasattr(implementation, "main"):
            assert legacy.main is implementation.main
        if hasattr(implementation, "PROJECT_ROOT"):
            assert implementation.PROJECT_ROOT == PROJECT_ROOT
        if hasattr(implementation, "ROOT"):
            assert implementation.ROOT == PROJECT_ROOT


def test_runtime_ocr_index_and_zotero_key_entries_remain_importable() -> None:
    worker = importlib.import_module("scripts.run_chaptered_import_job_worker")
    ocr = importlib.import_module("scripts.import_book_ocr_layout_first")
    fts = importlib.import_module("scripts.status_retrieval_fts")
    package = importlib.import_module("scripts.package_zotero_inspiration_plugin")
    zotero = importlib.import_module("scripts.phase110k_p_d_import_alignment_hook_dry_run")

    assert callable(worker.main)
    assert callable(ocr.evaluate_candidate_quality_gate)
    assert callable(fts.main)
    assert callable(package.main)
    assert callable(zotero.main)


def test_legacy_batch_entry_delegates_to_runtime_implementation() -> None:
    wrapper = (SCRIPTS_ROOT / "start_notebook_ai_dev.bat").read_text(encoding="utf-8")
    assert r'runtime\start_notebook_ai_dev.bat' in wrapper
    assert "%*" in wrapper

