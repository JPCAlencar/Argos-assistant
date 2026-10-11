from app.agent import AgentEngine
from app.database import Database
from app.executor import ToolExecutor
from app.security import SecretStore
from app.store import Store
from app.tasks import TaskManager
from app.tools.desktop import register_desktop
from app.tools.filesystem import register_files
from app.tools.registry import ToolRegistry
from app.tools.system import register_system
from app.workflows import WorkflowEngine


class Services:
    def __init__(self, settings):
        self.settings = settings
        self.db = Database(settings.database_url)
        self.store = Store(self.db, SecretStore(settings.master_key))
        self.registry = ToolRegistry()
        register_system(self.registry)
        register_files(self.registry, settings.allowed_roots)
        register_desktop(self.registry, settings.data_dir)
        self.executor = ToolExecutor(self)
        self.workflows = WorkflowEngine(self)
        self.workflows.register()
        self.agent = AgentEngine(self)
        self.tasks = TaskManager(self)
