/** Readable excerpt of common Markdown; the original remains available for inspection. */
export function fragmentText(markdown: string): string {
  let fence = ''
  return markdown.replace(/\r\n?/g, '\n').split('\n').map(line => {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/)
    if (marker && (!fence || marker[1][0] === fence[0] && marker[1].length >= fence.length)) {
      fence = fence ? '' : marker[1]
      return ''
    }
    if (fence || /^( {4}|\t)/.test(line)) return line
    if (/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line)
      || /^\s{0,3}(?:\*\s*){3,}$/.test(line)
      || /^\s{0,3}(?:_\s*){3,}$/.test(line)) return ''
    const code: string[] = []
    let value = line.replace(/(`+)(.+?)\1/g, (_, _ticks, content) => {
      code.push(content)
      return `\u0000${code.length - 1}\u0000`
    })
    value = value
      .replace(/^\s{0,3}(?:>\s*)+/, '')
      .replace(/^\s{0,3}#{1,6}\s+/, '').replace(/\s+#+\s*$/, '')
      .replace(/^\s*[-+*]\s+\[([ xX])\]\s+/, (_, checked) => checked.trim() ? '☑ ' : '☐ ')
      .replace(/^\s*[-+*]\s+/, '• ')
      .replace(/!?\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g, '$1（$2）')
      .replace(/\*\*(.+?)\*\*/g, '$1').replace(/__(.+?)__/g, '$1')
      .replace(/~~(.+?)~~/g, '$1')
      .replace(/\*([^*\n]+)\*/g, '$1')
      .replace(/(^|\s)_([^_\n]+)_(?=\s|$|[，。！？,.!?])/g, '$1$2')
      .replace(/\\([\\`*{}\[\]()#+.!_>~-])/g, '$1')
    if (/^\s*\|.*\|\s*$/.test(value)) value = value.trim().slice(1, -1).split('|').map(cell => cell.trim()).join(' ｜ ')
    return value.replace(/\u0000(\d+)\u0000/g, (_, index) => code[Number(index)])
  }).join('\n').replace(/\n{3,}/g, '\n\n').trim()
}
