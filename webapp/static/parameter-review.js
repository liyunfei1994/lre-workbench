import {parameterGroups} from "./parameter-editor.js";

// Accept only a verifiable review of the exact old values. Moving numerical
// fields out of a physical group must not fabricate or erase its acceptance.
export async function migrateWorkflowReview({reviews,inputs,group,legacyGroups,previousGroups,signatureFor,digest}){
  const review=reviews?.[group.id];
  if(!review||!["confirmed","preset"].includes(review.kind))return null;
  // Merged readonly identities are already covered by the architecture signature.
  const reviewedFields=group.fields.filter(field=>!(group.engine_definition&&field.read_only&&["cycle_type","propellant_pair"].includes(field.key)));
  for(const candidates of [previousGroups,legacyGroups]){
    const legacy=parameterGroups(candidates||[],inputs).find(item=>item.id===group.id);
    if(!legacy||!reviewedFields.every(field=>legacy.fields.some(old=>old.key===field.key)))continue;
    const previous=signatureFor(legacy,inputs);
    if(review.signature===previous || review.signature===await digest(previous))
      return {kind:review.kind,signature:signatureFor(group,inputs)};
  }
  return null;
}
