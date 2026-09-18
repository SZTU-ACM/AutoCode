import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'autocode-dsh-extension'
export const inject = []

export function apply(ctx) {
  const currentDir = path.dirname(fileURLToPath(import.meta.url))
  const candidateSkillsDirs = [
    path.resolve(currentDir, '..', 'skills'),
    path.resolve(currentDir, 'skills')
  ]

  ctx.on('ready', () => {
    try {
      const skillsDir = candidateSkillsDirs.find(d => fs.existsSync(d))
      if (skillsDir) {
        const skillEntries = fs.readdirSync(skillsDir, { withFileTypes: true })
        const availableSkills = skillEntries
          .filter(entry => entry.isDirectory())
          .map(entry => entry.name)

        if (ctx.logger) {
          ctx.logger('autocode').info(`AutoCode DSH plugin loaded with skills: ${availableSkills.join(', ')}`)
        }
      }
    } catch (error) {
      if (ctx.logger) {
        ctx.logger('autocode').warn(`AutoCode DSH plugin initialized with warning: ${error}`)
      }
    }
  })
}
