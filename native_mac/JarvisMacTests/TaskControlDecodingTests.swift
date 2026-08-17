import XCTest

@testable import Jarvis

/// Décodage des contrats serveur.
///
/// Les charges utiles sont copiées du contrat réel (`snake_case`, champs
/// optionnels absents). Un test qui décoderait une charge utile inventée ne
/// dirait rien du jour où le serveur change de forme.
final class TaskControlDecodingTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    func testTaskDecodesFromServerPayload() throws {
        let task = try decode(
            ControlTask.self,
            """
            {
              "task_id": "task_abc",
              "profile_id": "default",
              "title": "Préparer le rapport",
              "description": "",
              "status": "awaiting_plan_approval",
              "priority": "high",
              "source": {"source_type": "email", "channel": "email", "reference": "email:1",
                         "excerpt": "", "confidence": 0.62, "detection_reason": "formulation de demande",
                         "sender": "", "subject": ""},
              "project_id": null,
              "conversation_id": null,
              "due_at": null,
              "created_at": "2026-08-13T09:00:00+00:00",
              "updated_at": "2026-08-13T09:00:00+00:00",
              "plan_id": "plan_1",
              "plan_version": 1,
              "approved_plan_version": null,
              "approved_plan_digest": null,
              "agentic_run_id": null,
              "current_phase": "awaiting_plan_approval",
              "progress": 0.0,
              "attention_required": true,
              "result_status": null,
              "final_report_id": null,
              "legacy_task_id": null
            }
            """
        )
        XCTAssertEqual(task.status, .awaitingPlanApproval)
        XCTAssertEqual(task.source.sourceType, .email)
        XCTAssertTrue(task.isDecidable)
        XCTAssertNil(task.approvedPlanVersion)
    }

    /// Un état inconnu ne doit pas faire échouer le décodage : une version
    /// serveur plus récente ne doit pas vider l'écran.
    func testUnknownStatusFallsBackInsteadOfFailing() throws {
        struct Wrapper: Decodable { let status: TaskControlStatus }
        let wrapper = try decode(Wrapper.self, #"{"status": "hyperdrive"}"#)
        XCTAssertEqual(wrapper.status, .unknown)
        XCTAssertFalse(wrapper.status.needsAttention)
        XCTAssertFalse(wrapper.status.isExecuting)
    }

    func testUnknownSourceTypeFallsBack() throws {
        struct Wrapper: Decodable { let source: TaskControlSource }
        let wrapper = try decode(
            Wrapper.self,
            #"{"source": {"source_type": "telepathy", "channel": "api"}}"#
        )
        XCTAssertEqual(wrapper.source.sourceType, .unknown)
    }

    func testPlanDecodesStepsAndFlagsExternalEffects() throws {
        let plan = try decode(
            TaskPlan.self,
            """
            {
              "plan_id": "plan_1", "task_id": "task_abc", "version": 2,
              "objective": "Envoyer le rapport", "summary": "Deux étapes",
              "context_understood": "",
              "steps": [
                {"index": 1, "title": "Rédiger", "detail": "", "expected_result": "",
                 "tools": ["write_file"], "permissions": ["workspace:write"]},
                {"index": 2, "title": "Envoyer", "detail": "", "expected_result": "",
                 "tools": ["mail_send"], "permissions": ["mail:send"]}
              ],
              "expected_deliverables": ["Rapport"],
              "tools_expected": ["write_file", "mail_send"],
              "permissions_expected": ["workspace:write", "mail:send"],
              "execution_permissions": ["workspace:read", "workspace:write", "tests:run"],
              "risks": [], "assumptions": [], "success_criteria": [], "known_limits": [],
              "estimated_duration_s": 1800, "estimated_cost": null,
              "created_by": "jarvis.planner", "created_at": "2026-08-13T09:00:00+00:00",
              "decision": "pending", "decision_at": null, "decision_by": null,
              "decision_comment": "", "digest": "abc123"
            }
            """
        )
        XCTAssertEqual(plan.steps.count, 2)
        XCTAssertEqual(plan.externalEffectPermissions, ["mail:send"])
        XCTAssertEqual(plan.estimatedDurationLabel, "environ 30 min")
        // La liste affichée avant la décision est exactement celle du serveur,
        // sans réordonnancement ni complétion côté client.
        XCTAssertEqual(
            plan.executionPermissions,
            ["workspace:read", "workspace:write", "tests:run"]
        )
    }

    /// Un plan servi par un backend antérieur au contrat reste lisible, et la
    /// liste d'autorisations reste vide : l'application n'en invente aucune.
    func testPlanSansPermissionsDexecutionResteDecodable() throws {
        let plan = try decode(
            TaskPlan.self,
            """
            {
              "plan_id": "plan_2", "task_id": "task_abc", "version": 1,
              "objective": "Analyser", "summary": "",
              "context_understood": "",
              "steps": [
                {"index": 1, "title": "Lire", "detail": "", "expected_result": "",
                 "tools": [], "permissions": []}
              ],
              "expected_deliverables": [], "tools_expected": [],
              "permissions_expected": [],
              "risks": [], "assumptions": [], "success_criteria": [], "known_limits": [],
              "estimated_duration_s": null, "estimated_cost": null,
              "created_by": "jarvis.planner", "created_at": "2026-08-13T09:00:00+00:00",
              "decision": "pending", "decision_at": null, "decision_by": null,
              "decision_comment": "", "digest": "def456"
            }
            """
        )
        XCTAssertNil(plan.executionPermissions)
        XCTAssertEqual(plan.executionPermissions ?? [], [])
    }

    func testReportExtractsDeliveriesFromNestedData() throws {
        let report = try decode(
            TaskReport.self,
            """
            {
              "report_id": "report_1", "task_id": "task_abc", "version": 1,
              "result_status": "completed", "summary": "Tâche terminée.",
              "markdown": "# Titre",
              "data": {"deliveries": [
                 {"type": "file", "reference": "data/outputs/bilan.md", "sha256": ""},
                 {"type": "pull_request", "reference": "https://example.invalid/pr/1", "sha256": ""}
              ]},
              "created_at": "2026-08-13T10:00:00+00:00"
            }
            """
        )
        XCTAssertEqual(report.deliveries.count, 2)
        XCTAssertEqual(report.deliveries[0].label, "Fichier")
        XCTAssertEqual(report.deliveries[0].localPath, "data/outputs/bilan.md")
        XCTAssertNil(report.deliveries[0].openableURL)
        XCTAssertNotNil(report.deliveries[1].openableURL)
        XCTAssertNil(report.deliveries[1].localPath)
    }

    func testActivityEntryDecodesAndClassifies() throws {
        let entry = try decode(
            TaskActivityEntry.self,
            """
            {
              "activity_id": "act_1", "task_id": "task_abc", "run_id": "run_1",
              "sequence": 4, "event_type": "file_changed",
              "summary": "Modification d'un fichier", "agent_id": "executor",
              "agent_role": "executor", "phase": "", "tool_name": "write_file",
              "artifact_reference": "src/main.py", "status": "", "level": "detail",
              "created_at": "2026-08-13T09:05:00+00:00"
            }
            """
        )
        XCTAssertEqual(entry.roleLabel, "Exécution")
        XCTAssertEqual(entry.symbol, "doc.badge.gearshape")
        XCTAssertFalse(entry.isProblem)
    }

    func testApprovalDecodesSanitizedArgumentsAndPendingFlag() throws {
        let approval = try decode(
            TaskEffectApproval.self,
            """
            {
              "approval_id": "ap_1", "kind": "effect_approval",
              "action": "Envoyer un e-mail", "tool": "mail_send",
              "summary": "Envoi au fournisseur",
              "sanitized_arguments": {"destinataire": "contact@example.invalid"},
              "risks": ["Message sortant définitif"], "scope": "run",
              "expires_at": "2026-08-13T09:15:00+00:00", "decision": "pending",
              "decision_at": null
            }
            """
        )
        XCTAssertTrue(approval.isPending)
        XCTAssertEqual(approval.sanitizedArguments["destinataire"], "contact@example.invalid")
        XCTAssertEqual(approval.risks.count, 1)
    }

    func testDetailEnvelopeDecodesCurrentPlanAndComments() throws {
        let envelope = try decode(
            TaskDetailEnvelope.self,
            """
            {
              "task": {"task_id": "task_abc", "profile_id": "default", "title": "T",
                       "description": "", "status": "created", "priority": "medium",
                       "source": {"source_type": "manual", "channel": "macos"},
                       "current_phase": "", "progress": 0.0, "attention_required": false},
              "plans": [],
              "current_plan": null,
              "report": null,
              "comments": [{"comment_id": "cmt_1", "task_id": "task_abc",
                            "author": "session:1", "body": "note", "run_id": null,
                            "plan_version": null, "created_at": "2026-08-13T09:00:00+00:00"}]
            }
            """
        )
        XCTAssertNil(envelope.currentPlan)
        XCTAssertEqual(envelope.comments.first?.body, "note")
    }

    func testCandidateDecodesConfidenceLabel() throws {
        let candidate = try decode(
            TaskCandidate.self,
            """
            {
              "candidate_id": "cand_1", "profile_id": "default",
              "suggested_title": "Rappeler le fournisseur",
              "suggested_description": "", "confidence": 0.62,
              "source": {"source_type": "message", "channel": "imessage"},
              "reason": "formulation de demande", "suggested_due_at": null,
              "decision": "pending", "decision_at": null, "created_task_id": null,
              "duplicate_of": null, "created_at": "2026-08-13T09:00:00+00:00"
            }
            """
        )
        XCTAssertEqual(candidate.confidenceLabel, "62 %")
        XCTAssertEqual(candidate.decision, "pending")
    }
}

/// Propriétés de la machine à états côté client.
///
/// Elle ne décide rien — mais elle décide de ce que l'écran propose, et
/// proposer « Accepter et planifier » sur une tâche déjà lancée serait un
/// mensonge d'interface.
final class TaskControlStateTests: XCTestCase {

    func testNavigationSeparatesPersonalTodosFromJarvisMissions() {
        XCTAssertEqual(AppSection.missions.title, "Missions Jarvis")
        XCTAssertEqual(AppSection.todos.title, "À faire")
        XCTAssertEqual(AppSection.missions.sidebarHint, "Jarvis planifie et exécute")
        XCTAssertEqual(AppSection.todos.sidebarHint, "Votre liste à cocher")
        XCTAssertNotEqual(AppSection.missions.symbol, AppSection.todos.symbol)
    }

    func testEveryMissionSectionExplainsItsPurpose() {
        for section in TaskControlSection.allCases {
            XCTAssertFalse(section.subtitle.isEmpty, "\(section.rawValue) doit être explicite")
        }
    }

    func testOnlyAwaitingApprovalIsDecidable() {
        for status in TaskControlStatus.allCases {
            let task = ControlTask(
                taskID: "task_x", title: "T", description: "", status: status,
                priority: "medium", source: TaskControlSource(), projectID: nil,
                conversationID: nil, dueAt: nil, createdAt: nil, updatedAt: nil,
                planVersion: nil, approvedPlanVersion: nil, approvedPlanDigest: nil,
                agenticRunID: nil, currentPhase: "", progress: 0,
                attentionRequired: false, resultStatus: nil, finalReportID: nil
            )
            XCTAssertEqual(
                task.isDecidable, status == .awaitingPlanApproval,
                "\(status.rawValue) ne devrait pas être décidable"
            )
        }
    }

    func testAttentionStatesAreExactlyThree() {
        let attention = TaskControlStatus.allCases.filter(\.needsAttention)
        XCTAssertEqual(
            Set(attention),
            [.awaitingPlanApproval, .awaitingPermission, .blocked]
        )
    }

    func testPreApprovalStatesAreNeverExecuting() {
        for status in [
            TaskControlStatus.candidate, .created, .planning, .awaitingPlanApproval,
            .planRejected, .planRevisionRequested, .approved,
        ] {
            XCTAssertFalse(status.isExecuting, "\(status.rawValue) ne s'exécute pas")
        }
    }

    func testTerminalStatesAreNotCancellable() {
        for status in [TaskControlStatus.cancelled, .archived, .completed] {
            let task = ControlTask(
                taskID: "task_x", title: "T", description: "", status: status,
                priority: "medium", source: TaskControlSource(), projectID: nil,
                conversationID: nil, dueAt: nil, createdAt: nil, updatedAt: nil,
                planVersion: nil, approvedPlanVersion: nil, approvedPlanDigest: nil,
                agenticRunID: nil, currentPhase: "", progress: 0,
                attentionRequired: false, resultStatus: nil, finalReportID: nil
            )
            XCTAssertFalse(task.isCancellable)
        }
    }

    func testCandidateSectionIsTheOnlyNonTaskSection() {
        let candidateSections = TaskControlSection.allCases.filter(\.isCandidateSection)
        XCTAssertEqual(candidateSections, [.candidates])
    }
}
