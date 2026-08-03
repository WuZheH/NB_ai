# CREAD-A12 FIX3 Source Review

Source repository: WuZheH/NB_ai
Source branch: fix/cread-a11-local-pdf-source-binding
Source commit: d1a022e9ea7093ce660037e82d5b2a05301439ce
Runtime build: 20260803-search-0.1.4-cread-a12-d1a022e9

Confirmed root cause:

DOCUMENT_SOURCE_FILTER_FAILURE

Current search path:

notebook_search_service
-> high_quality_search_service
-> local_reranker_service
-> local_embedding_service
-> vector_store_service.search_passage_vectors

Current defect:

document_ids is applied only after global vector recall and reranking.
The required fix is to push the validated document constraint into
LanceDB before limit(), while retaining post-filtering as defense in depth.

No fix has been applied in this snapshot.
