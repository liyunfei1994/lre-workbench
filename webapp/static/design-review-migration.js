// Keep these lists in the order used by the version 16 system editor.
// cooling_dp_pa was already excluded from the former common pressure group.
const LEGACY_PRESSURE_FIELDS = {
  pressure_ffsc: [
    "preburner_injector_dp_ratio",
    "ox_turbine_exhaust_dp_ratio",
    "fuel_turbine_exhaust_dp_ratio",
  ],
  pressure: ["valve_line_dp_ratio"],
};

const BRANCH_PRESSURE_FIELDS = [
  "fuel_to_opb_line_dp_ratio",
  "fuel_to_opb_injector_dp_ratio",
  "fuel_to_fpb_line_dp_ratio",
  "fuel_to_fpb_injector_dp_ratio",
  "ox_to_opb_line_dp_ratio",
  "ox_to_opb_injector_dp_ratio",
  "ox_to_fpb_line_dp_ratio",
  "ox_to_fpb_injector_dp_ratio",
];

/**
 * Restore review metadata when the two legacy FFSC pressure groups are merged.
 * The caller supplies the version 16 group-signature function and digest helper.
 * Both former groups must still match their reviewed inputs. New branch-specific
 * values require a fresh review, even when equal to the inherited defaults.
 *
 * Returns an in-memory review with a raw signature, or null. The caller owns
 * persistence, project/revision race guards, and removal of obsolete group IDs.
 */
export async function migratePressureReview({reviews, inputs, group, signatureFor, digest}) {
  if (inputs?.cycle_type !== "ffsc" || group?.id !== "pressure_ffsc") return null;
  if (BRANCH_PRESSURE_FIELDS.some(key => inputs[key] != null)) return null;

  const legacyReviews = Object.keys(LEGACY_PRESSURE_FIELDS).map(id => reviews?.[id]);
  if (legacyReviews.some(review => !review ||
      !["confirmed", "preset"].includes(review.kind) ||
      typeof review.signature !== "string" || !review.signature)) return null;

  // Capture this before awaiting hashes: a user can edit the draft while the
  // browser is computing digests. The caller rechecks this signature afterwards.
  const mergedSignature = signatureFor(group, inputs);
  const matches = await Promise.all(Object.entries(LEGACY_PRESSURE_FIELDS).map(async ([id, keys]) => {
    const signature = signatureFor({fields: keys.map(key => ({key}))}, inputs);
    return reviews[id].signature === signature || reviews[id].signature === await digest(signature);
  }));
  if (!matches.every(Boolean)) return null;

  return {
    kind: legacyReviews.every(review => review.kind === "preset") ? "preset" : "confirmed",
    signature: mergedSignature,
  };
}
