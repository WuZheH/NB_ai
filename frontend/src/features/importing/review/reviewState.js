import { createEmptyEvidenceRef } from "./reviewModel.js";

function updateReviewItem(state, index, updater) {
  const items = [...state.reviewItems];
  items[index] = updater(items[index]);
  return { ...state, reviewItems: items };
}

export function toggleReviewStatus(state, index, status) {
  return updateReviewItem(state, index, item => ({ ...item, reviewStatus: status }));
}

export function editReviewTag(state, index, layer, tagIndex, value) {
  return updateReviewItem(state, index, item => {
    const tags = [...item.editedTags[layer]];
    if (tagIndex < tags.length) tags[tagIndex] = value;
    return { ...item, editedTags: { ...item.editedTags, [layer]: tags } };
  });
}

export function removeReviewTag(state, index, layer, tagIndex) {
  return updateReviewItem(state, index, item => {
    const tags = [...item.editedTags[layer]];
    tags.splice(tagIndex, 1);
    return { ...item, editedTags: { ...item.editedTags, [layer]: tags } };
  });
}

export function addReviewTag(state, index, layer) {
  return updateReviewItem(state, index, item => ({
    ...item,
    editedTags: {
      ...item.editedTags,
      [layer]: [...item.editedTags[layer], ""],
    },
  }));
}

export function updateReviewComment(state, index, value) {
  return updateReviewItem(state, index, item => ({ ...item, userComment: value }));
}

export function editEvidenceField(state, index, refIndex, field, value) {
  return updateReviewItem(state, index, item => {
    const refs = [...item.editedEvidenceRefs];
    if (refIndex < refs.length) refs[refIndex] = { ...refs[refIndex], [field]: value };
    return { ...item, editedEvidenceRefs: refs };
  });
}

export function removeEvidenceRef(state, index, refIndex) {
  return updateReviewItem(state, index, item => {
    const refs = [...item.editedEvidenceRefs];
    refs.splice(refIndex, 1);
    return { ...item, editedEvidenceRefs: refs };
  });
}

export function addEvidenceRef(state, index) {
  return updateReviewItem(state, index, item => ({
    ...item,
    editedEvidenceRefs: [...item.editedEvidenceRefs, createEmptyEvidenceRef()],
  }));
}

export function selectEvidenceSection(state, index, refIndex, sectionId) {
  const section = state.sourceTraceSections.find(item => item.section_id === sectionId);
  if (!section) return state;
  return updateReviewItem(state, index, item => {
    const refs = [...item.editedEvidenceRefs];
    if (refIndex < refs.length) {
      refs[refIndex] = {
        ...refs[refIndex],
        section_id: section.section_id,
        section_title: section.title,
        pdf_page: section.pdf_page != null ? String(section.pdf_page) : refs[refIndex].pdf_page,
      };
    }
    return { ...item, editedEvidenceRefs: refs };
  });
}

export function createCommitPhase() {
  return {
    paper: { status: "pending" },
    remap: { status: "pending" },
    objects: { status: "pending" },
  };
}

export function updatePhaseEntry(phase, key, status, resultData = null) {
  return {
    ...phase,
    [key]: { status, ...(resultData ? { result: resultData } : {}) },
  };
}

export function updateCommitPhase(state, phase, status, resultData = null) {
  return {
    ...state,
    commitPhase: updatePhaseEntry(state.commitPhase, phase, status, resultData),
  };
}

export function resetCommitPipeline(state) {
  return {
    ...state,
    commitLoading: true,
    commitPhase: createCommitPhase(),
    confirmRemapFailed: false,
    saveStatus: "",
    saveResult: null,
  };
}
