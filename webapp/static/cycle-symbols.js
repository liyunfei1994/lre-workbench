// Section-style functional symbols. These are drawing coordinates, not measured
// equipment geometry. The ports match the schematic's existing flow connections.
export function cycleEquipmentSymbol(node) {
  if (node.type === 'tank') return `
    <path class="cy-symbol" d="M-40-10C-40-22-24-28 0-28S40-22 40-10V10C40 22 24 28 0 28S-40 22-40 10Z"/>
    <path class="cy-section" d="M-40-10C-40-22-24-28 0-28S40-22 40-10V10C40 22 24 28 0 28S-40 22-40 10ZM-36-10V10C-36 19-21 24 0 24S36 19 36 10V-10C36-19 21-24 0-24S-36-19-36-10Z" fill-rule="evenodd"/>
    <path class="cy-detail" d="M-36 1H36M-36 7H36M-36-10C-36-19-21-24 0-24S36-19 36-10M-36 10C-36 19-21 24 0 24S36 19 36 10"/>
    <path class="cy-centerline" d="M0-22V22"/>
    <path class="cy-metal" d="M-7 22H7V28H-7Z"/>`;

  if (node.type === 'pump') {
    const vanes = Array.from({length:9}, (_,i) =>
      `<path class="cy-detail" d="M8-4Q18-7 23-17" transform="rotate(${i*40})"/>`).join('');
    return `
      <circle class="cy-symbol" r="35"/>
      <path class="cy-section" d="M0-35A35 35 0 1 1 0 35A35 35 0 1 1 0-35ZM0-30A30 30 0 1 0 0 30A30 30 0 1 0 0-30Z" fill-rule="evenodd"/>
      <path class="cy-detail" d="M-5-30C16-33 31-17 30 2S17 29-1 27S-28 11-25-4S-12-25 2-23"/>
      <circle class="cy-detail" r="23"/>
      ${vanes}
      <circle class="cy-metal" r="8"/>
      <circle class="cy-detail" r="3"/>
      <path class="cy-centerline" d="M-31 0H31M0-31V31"/>
      <path class="cy-metal" d="M-7-35H7V-31H-7ZM-7 31H7V35H-7Z"/>`;
  }

  if (node.type === 'turbine') return `
    <path class="cy-symbol" d="M-30-35H30V-27L26 27V35H-26V27L-30-27Z"/>
    <path class="cy-section" d="M-30-27H-25L-21 27H-26ZM25-27H30L26 27H21Z"/>
    <path class="cy-metal" d="M-33-35H33V-29H-33ZM-29 29H29V35H-29ZM-35-3H35V3H-35Z"/>
    <path class="cy-detail" d="M-25-25H25M-23-12H23M-22 14H22M-21 25H21M-21-24L-15-13M-11-24L-5-13M-1-24L5-13M9-24L15-13M19-24L23-17M-19 5L-23 13M-9 5L-15 13M1 5L-5 13M11 5L5 13M21 5L15 13M-19 17L-15 24M-9 17L-5 24M1 17L5 24M11 17L15 24"/>
    <path class="cy-centerline" d="M-39 0H39M0-27V27"/>`;

  if (node.type === 'cooling') return `
    <rect class="cy-symbol" x="-64" y="-36" width="128" height="72" rx="3"/>
    <path class="cy-section" d="M-64-36H64V36H-64ZM-58-30V30H58V-30Z" fill-rule="evenodd"/>
    <path class="cy-metal" d="M-54-24H54V-14H-54ZM-54 14H54V24H-54Z"/>
    <path class="cy-detail" d="M-36-14V14M-30-14V14M-15-14V14M-9-14V14M9-14V14M15-14V14M30-14V14M36-14V14M-7-36V-24M7-36V-24M-7 24V36M7 24V36M-60-26L-56-30M-60-10L-56-14M-60 6L-56 2M-60 22L-56 18M56-18L60-22M56-2L60-6M56 14L60 10M56 30L60 26"/>
    <path class="cy-centerline" d="M0-12V12"/>`;

  if (node.type === 'preburner') return `
    <path class="cy-symbol" d="M-55-28H55V8Q55 26 18 28L10 36H-10L-18 28Q-55 26-55 8Z"/>
    <path class="cy-section" d="M-55-23H-49V8Q-49 21-16 22L-6 33V36H-10L-18 28Q-55 26-55 8ZM49-23H55V8Q55 26 18 28L10 36H6V33L16 22Q49 21 49 8Z"/>
    <path class="cy-metal" d="M-62-36H62V-29H-62ZM-55-23H55V-17H-55Z"/>
    <path class="cy-detail" d="M-50-36V-23M-30-36V-23M0-36V-23M30-36V-23M50-36V-23M-40-17V-9M-20-17V-9M0-17V-9M20-17V-9M40-17V-9M-45-7L-39 2M-25-7L-19 2M-5-7L1 2M15-7L21 2M35-7L41 2"/>
    <path class="cy-centerline" d="M0 7V29"/>
    <path class="cy-detail" d="M-5 21L0 29L5 21"/>`;

  return null;
}
