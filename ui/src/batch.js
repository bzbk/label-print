/**
 * 批量打印数据：CSV / 粘贴表格解析、占位符替换。
 *
 * 表格第一行为表头；文本元素中的 {{列名}} 在逐行打印时替换。
 * 支持逗号或制表符分隔、双引号包裹（"" 转义）、引号内换行。
 */

const PLACEHOLDER_RE = /\{\{\s*([^{}]+?)\s*\}\}/g

/** 收集当前文档文本元素里的占位符（去重，保持出现顺序） */
export function extractPlaceholders(doc) {
  const found = new Set()
  for (const item of doc?.items || []) {
    if (item.type !== 'text') continue
    for (const match of String(item.text ?? '').matchAll(PLACEHOLDER_RE)) {
      found.add(String(match[1]).trim())
    }
  }
  return [...found]
}

/** 把一行数据替换进文本模板；未知占位符原样保留 */
export function renderTemplateText(text, row) {
  return String(text ?? '').replace(PLACEHOLDER_RE, (matched, key) => {
    const name = String(key).trim()
    const value = row?.[name]
    return value === undefined || value === null ? matched : String(value)
  })
}

/** 基于当前 spec 生成某一行的打印 spec（只替换 text 元素） */
export function buildRowSpec(spec, row) {
  const copy = JSON.parse(JSON.stringify(spec))
  copy.items = (copy.items || []).map((item) =>
    item.type === 'text' ? { ...item, text: renderTemplateText(item.text, row) } : item,
  )
  return copy
}

function parseDelimited(text, delimiter) {
  const rows = []
  let row = []
  let field = ''
  let inQuotes = false
  const source = String(text ?? '').replace(/^\uFEFF/, '')
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (inQuotes) {
      if (char === '"') {
        if (source[index + 1] === '"') {
          field += '"'
          index += 1
        } else {
          inQuotes = false
        }
      } else {
        field += char
      }
      continue
    }
    if (char === '"') {
      inQuotes = true
    } else if (char === delimiter) {
      row.push(field)
      field = ''
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && source[index + 1] === '\n') index += 1
      row.push(field)
      rows.push(row)
      row = []
      field = ''
    } else {
      field += char
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field)
    rows.push(row)
  }
  return rows.filter((cells) => cells.some((cell) => String(cell).trim() !== ''))
}

/**
 * 解析 CSV / 制表符文本。
 * @returns {{ headers: string[], rows: Array<Record<string,string>>, error: string, delimiter: string }}
 */
export function parseTable(text) {
  const raw = String(text ?? '')
  if (!raw.trim()) return { headers: [], rows: [], error: '', delimiter: ',' }
  const firstLine = raw.split(/\r?\n/).find((line) => line.trim()) || ''
  const delimiter =
    firstLine.split('\t').length >= firstLine.split(',').length ? '\t' : ','
  const grid = parseDelimited(raw, delimiter)
  if (!grid.length) return { headers: [], rows: [], error: '没有可解析的数据', delimiter }
  const headers = grid[0].map((cell) => String(cell).trim())
  if (!headers.length || headers.every((header) => !header)) {
    return { headers: [], rows: [], error: '第一行必须是表头（列名）', delimiter }
  }
  const dedupedHeaders = []
  const used = new Map()
  for (const header of headers) {
    let name = header || `列${dedupedHeaders.length + 1}`
    const count = used.get(name) || 0
    used.set(name, count + 1)
    if (count > 0) name = `${name}_${count + 1}`
    dedupedHeaders.push(name)
  }
  const rows = grid.slice(1).map((cells) => {
    const record = {}
    dedupedHeaders.forEach((header, index) => {
      record[header] = String(cells[index] ?? '').trim()
    })
    return record
  })
  return { headers: dedupedHeaders, rows, error: '', delimiter }
}
