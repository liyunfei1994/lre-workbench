import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import {migratePressureReview} from "../webapp/static/design-review-migration.js";

const legacyFields = {
  pressure_ffsc: ["preburner_injector_dp_ratio", "ox_turbine_exhaust_dp_ratio", "fuel_turbine_exhaust_dp_ratio"],
  pressure: ["valve_line_dp_ratio"],
};
const branchFields = [
  "fuel_to_opb_line_dp_ratio", "fuel_to_opb_injector_dp_ratio",
  "fuel_to_fpb_line_dp_ratio", "fuel_to_fpb_injector_dp_ratio",
  "ox_to_opb_line_dp_ratio", "ox_to_opb_injector_dp_ratio",
  "ox_to_fpb_line_dp_ratio", "ox_to_fpb_injector_dp_ratio",
];
const inputs = {
  cycle_type: "ffsc", propellant_pair: "lox_methane", gg_layout: "dual_shaft",
  preburner_injector_dp_ratio: 0.12, ox_turbine_exhaust_dp_ratio: 0.243,
  fuel_turbine_exhaust_dp_ratio: 0.093, valve_line_dp_ratio: 0.03,
  cooling_dp_pa: 2e6,
  ...Object.fromEntries(branchFields.map(key => [key, null])),
};
const group = {id: "pressure_ffsc", fields: [
  "preburner_injector_dp_ratio", "valve_line_dp_ratio", ...branchFields,
  "ox_turbine_exhaust_dp_ratio", "fuel_turbine_exhaust_dp_ratio",
].map(key => ({key}))};

// This is the persisted signature contract of the version 16 system editor.
function signatureFor(group, values = inputs) {
  const architecture = [values.cycle_type, values.propellant_pair,
    values.cycle_type === "gg" ? values.gg_layout : null];
  return JSON.stringify([16, architecture, group.fields.map(field => [field.key, values[field.key]])]);
}
const digest = async signature => "sha256:" + createHash("sha256").update(signature).digest("hex");
function legacyReviews(kinds = ["confirmed", "confirmed"], values = inputs) {
  return Object.fromEntries(Object.entries(legacyFields).map(([id, keys], index) => [id, {
    kind: kinds[index], signature: signatureFor({fields: keys.map(key => ({key}))}, values),
  }]));
}
const migrate = overrides => migratePressureReview({reviews: legacyReviews(), inputs, group, signatureFor, digest, ...overrides});

test("two unchanged raw legacy reviews become one review with the merged raw signature", async () => {
  assert.deepEqual(await migrate(), {kind: "confirmed", signature: signatureFor(group)});
});

test("stored SHA-256 and mixed raw/hashed signatures restore without double hashing", async () => {
  const reviews = legacyReviews();
  reviews.pressure_ffsc.signature = await digest(reviews.pressure_ffsc.signature);
  assert.deepEqual(await migrate({reviews}), {kind: "confirmed", signature: signatureFor(group)});
  reviews.pressure.signature = await digest(reviews.pressure.signature);
  const restored = await migrate({reviews});
  assert.equal(restored.signature, signatureFor(group));
  assert.equal(await digest(restored.signature), await digest(signatureFor(group)));
});

test("both prior groups are necessary and stale metadata cannot confirm the merged group", async () => {
  for (const id of Object.keys(legacyFields)) {
    const missing = legacyReviews();
    delete missing[id];
    assert.equal(await migrate({reviews: missing}), null);
    const stale = legacyReviews();
    stale[id].signature += " ";
    assert.equal(await migrate({reviews: stale}), null);
    const hashed = legacyReviews();
    hashed[id].signature = await digest(stale[id].signature);
    assert.equal(await migrate({reviews: hashed}), null);
  }
});

test("changes to any old pressure input or the active architecture invalidate review inheritance", async () => {
  for (const key of Object.values(legacyFields).flat()) {
    assert.equal(await migrate({inputs: {...inputs, [key]: inputs[key] + 0.01}}), null, key);
  }
  assert.equal(await migrate({inputs: {...inputs, propellant_pair: "lox_hydrogen"}}), null);
  assert.equal(await migrate({inputs: {...inputs, cycle_type: "gg"}}), null);
  assert.equal(await migrate({group: {...group, id: "pressure"}}), null);
});

test("a new explicit branch budget requires review, including zero and inherited-equivalent values", async () => {
  for (const key of branchFields) {
    for (const value of [0, 0.03, 0.12, ""]) {
      assert.equal(await migrate({inputs: {...inputs, [key]: value}}), null, `${key}: ${JSON.stringify(value)}`);
    }
  }
});

test("absent and null branch overrides both retain legacy behavior and the same new signature", async () => {
  const absent = {...inputs};
  for (const key of branchFields) delete absent[key];
  assert.deepEqual(await migrate({inputs: absent}), await migrate());
  const undefinedValues = {...inputs, ...Object.fromEntries(branchFields.map(key => [key, undefined]))};
  assert.deepEqual(await migrate({inputs: undefinedValues}), await migrate());
});

test("review kind is preset only when both old groups were adopted as presets", async () => {
  for (const kinds of [["confirmed", "preset"], ["preset", "confirmed"], ["confirmed", "confirmed"], ["preset", "preset"]]) {
    const result = await migrate({reviews: legacyReviews(kinds)});
    assert.equal(result.kind, kinds.every(kind => kind === "preset") ? "preset" : "confirmed");
  }
  for (const id of Object.keys(legacyFields)) {
    for (const replacement of [null, {}, {kind:"filled", signature:"x"}, {kind:"confirmed", signature:null}]) {
      assert.equal(await migrate({reviews: {...legacyReviews(), [id]: replacement}}), null);
    }
  }
});

test("legacy field order and schema version must match exactly; cooling is not an old pressure-group field", async () => {
  const reordered = legacyReviews();
  reordered.pressure_ffsc.signature = signatureFor({fields:[...legacyFields.pressure_ffsc].reverse().map(key => ({key}))});
  assert.equal(await migrate({reviews: reordered}), null);
  const differentVersion = legacyReviews();
  differentVersion.pressure.signature = differentVersion.pressure.signature.replace("[16,", "[17,");
  assert.equal(await migrate({reviews: differentVersion}), null);
  const withCooling = legacyReviews();
  withCooling.pressure.signature = signatureFor({fields:["valve_line_dp_ratio", "cooling_dp_pa"].map(key => ({key}))});
  assert.equal(await migrate({reviews: withCooling}), null);
  assert.deepEqual(await migrate({inputs: {...inputs, cooling_dp_pa: 3e6}}), await migrate());
});

test("migration is side-effect free and leaves unrelated confirmations untouched", async () => {
  const reviews = {...legacyReviews(), cycle: {kind: "confirmed", signature: "unchanged-cycle-review"}};
  const before = structuredClone({reviews, inputs, group});
  await migrate({reviews});
  assert.deepEqual({reviews, inputs, group}, before);
});

test("digest failures propagate to the existing restore error handling", async () => {
  const reviews = legacyReviews();
  reviews.pressure.signature = await digest(reviews.pressure.signature);
  await assert.rejects(migrate({reviews, digest: async () => {throw new Error("digest unavailable");}}), /digest unavailable/);
});

test("a draft edit during asynchronous digest computation is not silently confirmed", async () => {
  const values = structuredClone(inputs);
  const reviews = legacyReviews();
  reviews.pressure.signature = await digest(reviews.pressure.signature);
  const expected = signatureFor(group, values);
  const migrated = await migrate({inputs: values, reviews, digest: async signature => {
    values.fuel_to_opb_line_dp_ratio = 0.06;
    return digest(signature);
  }});
  assert.equal(migrated.signature, expected);
  assert.notEqual(migrated.signature, signatureFor(group, values));
});
