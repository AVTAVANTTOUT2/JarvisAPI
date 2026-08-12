import { describe, expect, it } from 'vitest'

import { agenticEventLabel } from './ChatView'

describe('agenticEventLabel', () => {
  it('affiche le résumé final neutralisé fourni par JARVIS', () => {
    expect(agenticEventLabel('agent.run.completed', {
      spoken_summary: 'Les tests sont verts et le résultat est vérifié.',
    })).toBe('Les tests sont verts et le résultat est vérifié.')
  })

  it('conserve un libellé générique sans résumé public', () => {
    expect(agenticEventLabel('agent.run.completed', {})).toBe('Tâche terminée.')
  })
})
