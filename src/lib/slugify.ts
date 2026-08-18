/**
 * Turns a card title into a safe download filename stem, shared by ChartCard
 * and DiagramCard. NFD splits an accented letter into base + combining mark so
 * the marks can be stripped; `đ`/`Đ` has no decomposition, so it's mapped by
 * hand. Without this a Vietnamese title lost every accented letter outright —
 * "Tình trạng TEU" downloaded as "t-nh-tr-ng-teu", and an all-diacritic title
 * collapsed to the bare fallback.
 * Input: the title and the stem to use when nothing survives. Output: the stem.
 */
export function slugify(title: string, fallback: string): string {
  return (
    title
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[đĐ]/g, 'd')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || fallback
  )
}
