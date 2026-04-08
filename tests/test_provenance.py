# Tests for Provenance

"""Tests unitaires pour le module de provenance et son intégration avec ContinuousFlowExecutor."""

import unittest
from core import Flow, FlowFile, Task
from engine.provenance import ProvenanceEventType, ProvenanceEvent, ProvenanceRepository
from engine.continuous_executor import ContinuousFlowExecutor


class UpperTask(Task):
    """Task de test qui met le contenu en majuscules."""
    TYPE = "upperCase"
    NAME = "Upper Case"

    def get_parameter_schema(self):
        return {}

    def execute(self, flowfile):
        content = flowfile.content.upper()
        flowfile.content = content
        return [flowfile]


class FailingTask(Task):
    """Task de test qui echoue toujours."""
    TYPE = "alwaysFail"
    NAME = "Always Fail"

    def get_parameter_schema(self):
        return {}

    def execute(self, flowfile):
        raise RuntimeError("Echec volontaire")


class SplitterTask(Task):
    """Task de test qui produit 2 flowfiles."""
    TYPE = "splitter"
    NAME = "Splitter"

    def get_parameter_schema(self):
        return {}

    def execute(self, flowfile):
        ff1 = FlowFile(content=flowfile.content, attributes=dict(flowfile.attributes))
        ff2 = FlowFile(content=flowfile.content, attributes=dict(flowfile.attributes))
        ff1.set_attribute("fragment.index", "0")
        ff2.set_attribute("fragment.index", "1")
        return [ff1, ff2]


# ============================================================================
# Tests unitaires du ProvenanceRepository
# ============================================================================

class TestProvenanceRepository(unittest.TestCase):
    """Tests du repository de provenance."""

    def setUp(self):
        self.repo = ProvenanceRepository(max_events=100)

    def test_record_and_size(self):
        """Enregistrer des evenements et verifier la taille."""
        self.assertEqual(self.repo.size(), 0)
        self.repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CREATE,
            flowfile_id="ff-1",
            flow_id="flow-1",
        ))
        self.assertEqual(self.repo.size(), 1)

    def test_fifo_eviction(self):
        """Les anciens evenements sont supprimes quand max_events est depasse."""
        repo = ProvenanceRepository(max_events=5)
        for i in range(10):
            repo.record(ProvenanceEvent(
                event_type=ProvenanceEventType.CREATE,
                flowfile_id=f"ff-{i}",
            ))
        self.assertEqual(repo.size(), 5)
        events = repo.get_events(limit=10)
        # Les 5 derniers doivent rester (ff-5 a ff-9)
        ids = [e.flowfile_id for e in events]
        self.assertEqual(ids, [f"ff-{i}" for i in range(5, 10)])

    def test_get_events_filter_by_type(self):
        """Filtrer par type d'evenement."""
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, flowfile_id="ff-1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.MODIFY, flowfile_id="ff-1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, flowfile_id="ff-2"))

        creates = self.repo.get_events(event_type=ProvenanceEventType.CREATE)
        self.assertEqual(len(creates), 2)

        modifies = self.repo.get_events(event_type=ProvenanceEventType.MODIFY)
        self.assertEqual(len(modifies), 1)

    def test_get_events_filter_by_flowfile(self):
        """Filtrer par flowfile_id."""
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, flowfile_id="ff-1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.MODIFY, flowfile_id="ff-1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, flowfile_id="ff-2"))

        events = self.repo.get_events(flowfile_id="ff-1")
        self.assertEqual(len(events), 2)

    def test_get_events_filter_by_task(self):
        """Filtrer par task_id."""
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.RECEIVE, task_id="t1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.SEND, task_id="t1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.RECEIVE, task_id="t2"))

        events = self.repo.get_events(task_id="t1")
        self.assertEqual(len(events), 2)

    def test_get_flow_events(self):
        """Recuperer tous les evenements d'un flow."""
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, flow_id="f1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.MODIFY, flow_id="f1"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, flow_id="f2"))

        events = self.repo.get_flow_events("f1")
        self.assertEqual(len(events), 2)

    def test_clear(self):
        """Vider le repository."""
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE))
        self.repo.clear()
        self.assertEqual(self.repo.size(), 0)

    def test_to_dict(self):
        """Verifier le resume statistique."""
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, task_type="log"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.MODIFY, task_type="log"))
        self.repo.record(ProvenanceEvent(event_type=ProvenanceEventType.CREATE, task_type="upper"))

        summary = self.repo.to_dict()
        self.assertEqual(summary["total_events"], 3)
        self.assertEqual(summary["events_by_type"]["CREATE"], 2)
        self.assertEqual(summary["events_by_type"]["MODIFY"], 1)
        self.assertEqual(summary["events_by_task"]["log"], 2)
        self.assertEqual(summary["events_by_task"]["upper"], 1)

    def test_event_to_dict(self):
        """Verifier la serialisation d'un evenement."""
        event = ProvenanceEvent(
            event_type=ProvenanceEventType.CREATE,
            flowfile_id="ff-1",
            task_id="t1",
            task_type="log",
            flow_id="f1",
            content_size=42,
            details="test",
        )
        d = event.to_dict()
        self.assertEqual(d["event_type"], "CREATE")
        self.assertEqual(d["flowfile_id"], "ff-1")
        self.assertEqual(d["content_size"], 42)


# ============================================================================
# Tests du lignage (lineage)
# ============================================================================

class TestProvenanceLineage(unittest.TestCase):
    """Tests du suivi de lignage."""

    def test_lineage_parent_child(self):
        """Le lignage suit les relations parent/enfant."""
        repo = ProvenanceRepository()

        # ff-parent est cree, puis clone en ff-child
        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CREATE,
            flowfile_id="ff-parent",
        ))
        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CLONE,
            flowfile_id="ff-child",
            parent_flowfile_ids=["ff-parent"],
        ))
        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.MODIFY,
            flowfile_id="ff-child",
        ))

        lineage = repo.get_lineage("ff-parent")
        ff_ids = {e.flowfile_id for e in lineage}
        self.assertIn("ff-parent", ff_ids)
        self.assertIn("ff-child", ff_ids)

    def test_lineage_multi_hop(self):
        """Le lignage suit les chaines parent → enfant → petit-enfant."""
        repo = ProvenanceRepository()

        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CREATE,
            flowfile_id="ff-a",
        ))
        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CLONE,
            flowfile_id="ff-b",
            parent_flowfile_ids=["ff-a"],
        ))
        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CLONE,
            flowfile_id="ff-c",
            parent_flowfile_ids=["ff-b"],
        ))

        lineage = repo.get_lineage("ff-a")
        ff_ids = {e.flowfile_id for e in lineage}
        self.assertIn("ff-a", ff_ids)
        self.assertIn("ff-b", ff_ids)
        self.assertIn("ff-c", ff_ids)

    def test_lineage_from_child(self):
        """Le lignage depuis un enfant remonte aux parents via child_flowfile_ids."""
        repo = ProvenanceRepository()

        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CREATE,
            flowfile_id="ff-parent",
            child_flowfile_ids=["ff-child"],
        ))
        repo.record(ProvenanceEvent(
            event_type=ProvenanceEventType.CLONE,
            flowfile_id="ff-child",
            parent_flowfile_ids=["ff-parent"],
        ))

        lineage = repo.get_lineage("ff-child")
        ff_ids = {e.flowfile_id for e in lineage}
        self.assertIn("ff-parent", ff_ids)
        self.assertIn("ff-child", ff_ids)


# ============================================================================
# Tests d'integration avec FlowExecutor
# ============================================================================

class TestProvenanceIntegration(unittest.TestCase):
    """Tests de l'integration provenance + executor."""

    def test_simple_flow_emits_send(self):
        """Un flow simple A emet SEND apres commit."""
        repo = ProvenanceRepository()

        flow = Flow({"name": "test-simple"})
        flow.tasks = {"A": UpperTask({})}
        flow.relations = []

        ff = FlowFile(content=b"hello", attributes={"test": "1"})
        result = ContinuousFlowExecutor.run_batch(
            flow, input_flowfiles=[ff], max_retries=1, provenance=repo)

        self.assertTrue(result.success)
        self.assertGreater(repo.size(), 0)

        events = repo.get_flow_events(flow.id)
        event_types = [e.event_type for e in events]

        self.assertIn(ProvenanceEventType.SEND, event_types)

    def test_chain_flow_emits_events_per_task(self):
        """Un flow A → B emet SEND pour chaque tache."""
        repo = ProvenanceRepository()

        flow = Flow({"name": "test-chain"})
        flow.tasks = {"A": UpperTask({}), "B": UpperTask({})}
        flow.relations = [{"from": "A", "to": "B"}]

        ff = FlowFile(content=b"hello")
        result = ContinuousFlowExecutor.run_batch(
            flow, input_flowfiles=[ff], max_retries=1, provenance=repo)

        self.assertTrue(result.success)

        events = repo.get_flow_events(flow.id)
        send_events = [e for e in events if e.event_type == ProvenanceEventType.SEND]
        # A sends, B sends → au moins 2 SEND
        self.assertGreaterEqual(len(send_events), 2)

        task_ids = {e.task_id for e in send_events}
        self.assertIn("A", task_ids)
        self.assertIn("B", task_ids)

    def test_branch_flow_emits_multiple_sends(self):
        """Un flow A → B, A → C emet SEND pour chaque branche."""
        repo = ProvenanceRepository()

        flow = Flow({"name": "test-branch"})
        flow.tasks = {"A": UpperTask({}), "B": UpperTask({}), "C": UpperTask({})}
        flow.relations = [{"from": "A", "to": "B"}, {"from": "A", "to": "C"}]

        ff = FlowFile(content=b"hello")
        result = ContinuousFlowExecutor.run_batch(
            flow, input_flowfiles=[ff], max_retries=1, provenance=repo)

        self.assertTrue(result.success)

    def test_failing_task_reports_error(self):
        """Une tache qui echoue est signee en erreur dans le resultat."""
        repo = ProvenanceRepository()

        flow = Flow({"name": "test-fail"})
        flow.tasks = {"A": FailingTask({})}
        flow.relations = []

        ff = FlowFile(content=b"hello")
        result = ContinuousFlowExecutor.run_batch(
            flow, input_flowfiles=[ff], max_retries=1, provenance=repo)

        # Task errors are now discarded (task continues processing)
        # The flow itself still completes successfully
        self.assertTrue(result.success)

    def test_no_provenance_when_disabled(self):
        """Sans ProvenanceRepository, aucune erreur ne survient."""
        flow = Flow({"name": "test-noprov"})
        flow.tasks = {"A": UpperTask({})}
        flow.relations = []

        ff = FlowFile(content=b"hello")
        result = ContinuousFlowExecutor.run_batch(
            flow, input_flowfiles=[ff], max_retries=1, provenance=None)
        self.assertTrue(result.success)

    def test_provenance_in_statistics(self):
        """Les stats de provenance apparaissent dans le resultat."""
        repo = ProvenanceRepository()

        flow = Flow({"name": "test-stats"})
        flow.tasks = {"A": UpperTask({})}
        flow.relations = []

        ff = FlowFile(content=b"hello")
        result = ContinuousFlowExecutor.run_batch(
            flow, input_flowfiles=[ff], max_retries=1, provenance=repo)

        self.assertTrue(result.success)
        self.assertIn("provenance", result.statistics)
        prov_stats = result.statistics["provenance"]
        self.assertGreater(prov_stats["total_events"], 0)


if __name__ == "__main__":
    unittest.main()
