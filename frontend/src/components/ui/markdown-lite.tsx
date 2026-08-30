import { Fragment } from "react"

// A narrow, dependency-free renderer for the handful of markdown constructs
// agent-generated justification/reasoning text actually uses: **bold**
// spans, "- "/"* " bullet lists (including a lead-in line followed by a
// bullet list within the same paragraph block), and blank-line-separated
// paragraphs. Anything else (headings, links, code) is left as literal text
// rather than mis-rendered — this is not a general markdown parser, just
// enough to stop **bold** and bullet markers from leaking into the UI as
// raw asterisks/dashes.

function renderInline(text: string, keyPrefix: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean)
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
    }
    return <Fragment key={`${keyPrefix}-${i}`}>{part}</Fragment>
  })
}

function isBulletLine(line: string) {
  return /^\s*[-*]\s+/.test(line)
}

// Groups a block's non-empty lines into consecutive runs of the same kind
// (bullet vs. plain text) — a lead-in sentence followed by a bullet list is
// two runs, not one mixed block that fails an "every line is a bullet" test.
function groupRuns(lines: string[]): { bullet: boolean; lines: string[] }[] {
  const runs: { bullet: boolean; lines: string[] }[] = []
  for (const line of lines) {
    const bullet = isBulletLine(line)
    const last = runs[runs.length - 1]
    if (last && last.bullet === bullet) {
      last.lines.push(line)
    } else {
      runs.push({ bullet, lines: [line] })
    }
  }
  return runs
}

export function MarkdownLite({ text, className }: { text: string; className?: string }) {
  if (!text) return null

  const blocks = text.split(/\n{2,}/)

  return (
    <div className={className}>
      {blocks.map((block, blockIndex) => {
        const lines = block.split("\n").filter((l) => l.trim().length > 0)
        const runs = groupRuns(lines)

        return (
          <div key={blockIndex} className={blockIndex > 0 ? "mt-2" : undefined}>
            {runs.map((run, runIndex) => {
              const keyPrefix = `${blockIndex}-${runIndex}`
              if (run.bullet) {
                return (
                  <ul key={keyPrefix} className={runIndex > 0 ? "list-disc pl-5 space-y-1 mt-1" : "list-disc pl-5 space-y-1"}>
                    {run.lines.map((line, i) => (
                      <li key={i}>{renderInline(line.replace(/^\s*[-*]\s+/, ""), `${keyPrefix}-${i}`)}</li>
                    ))}
                  </ul>
                )
              }
              return (
                <p key={keyPrefix} className={runIndex > 0 ? "mt-1" : undefined}>
                  {run.lines.map((line, i, arr) => (
                    <Fragment key={i}>
                      {renderInline(line, `${keyPrefix}-${i}`)}
                      {i < arr.length - 1 && <br />}
                    </Fragment>
                  ))}
                </p>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
