"""Docker Engine and Kubernetes cluster inspection with non-blocking offline fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple
import subprocess


@dataclass
class ContainerSummary:
    """Summary of a Docker container."""

    short_id: str
    name: str
    image: str
    status: str
    ports: str


@dataclass
class PodSummary:
    """Summary of a Kubernetes Pod."""

    name: str
    namespace: str
    phase: str
    restarts: int = 0


class InfraObserver:
    """Inspects local Docker containers and Kubernetes pods with graceful fallbacks."""

    def get_docker_containers(self, all_containers: bool = False) -> Tuple[bool, str, List[ContainerSummary]]:
        """Query active Docker containers via Docker SDK or CLI fallback.

        Returns:
            Tuple of (is_online, status_label, list_of_containers).
        """
        try:
            import docker  # type: ignore

            client = docker.from_env(timeout=2)
            client.ping()
            raw_list = client.containers.list(all=all_containers)
            summaries: List[ContainerSummary] = []
            for c in raw_list:
                image_tags = c.image.tags if getattr(c, "image", None) else []
                image_name = image_tags[0] if image_tags else c.attrs.get("Config", {}).get("Image", "unknown")
                ports_dict = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
                port_bindings = []
                for container_port, host_bindings in ports_dict.items():
                    if host_bindings:
                        host_port = host_bindings[0].get("HostPort", "")
                        port_bindings.append(f"{host_port}->{container_port}")
                    else:
                        port_bindings.append(str(container_port))
                summaries.append(
                    ContainerSummary(
                        short_id=c.short_id,
                        name=c.name,
                        image=image_name,
                        status=c.status,
                        ports=", ".join(port_bindings) if port_bindings else "-",
                    )
                )
            return True, f"Online ({len(summaries)} containers)", summaries
        except Exception:
            # Try lightweight docker CLI fallback in case SDK isn't installed or socket permission differs
            return self._docker_cli_fallback(all_containers=all_containers)

    def _docker_cli_fallback(self, all_containers: bool = False) -> Tuple[bool, str, List[ContainerSummary]]:
        cmd = ["docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"]
        if all_containers:
            cmd.insert(2, "-a")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, check=False)
            if proc.returncode != 0:
                return False, "Offline", []
            summaries: List[ContainerSummary] = []
            for line in proc.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 4:
                    summaries.append(
                        ContainerSummary(
                            short_id=parts[0][:12],
                            name=parts[1],
                            image=parts[2],
                            status=parts[3],
                            ports=parts[4] if len(parts) > 4 and parts[4] else "-",
                        )
                    )
            return True, f"Online ({len(summaries)} containers)", summaries
        except Exception:
            return False, "Offline", []

    def get_k8s_pods(self, namespace: str | None = None) -> Tuple[bool, str, List[PodSummary]]:
        """Inspect Kubernetes pods from ~/.kube/config via python client or kubectl fallback.

        Returns:
            Tuple of (is_online, context_name_or_offline, list_of_pods).
        """
        try:
            from kubernetes import client, config as k8s_config  # type: ignore

            contexts, active_context = k8s_config.list_kube_config_contexts()
            if not active_context:
                return False, "Offline (No Context)", []
            context_name = active_context.get("name", "default")
            active_ns = namespace or active_context.get("context", {}).get("namespace", "default")

            k8s_config.load_kube_config()
            v1 = client.CoreV1Api()
            pod_list = v1.list_namespaced_pod(namespace=active_ns, _request_timeout=2.0)

            pods: List[PodSummary] = []
            for item in pod_list.items:
                phase = item.status.phase or "Unknown"
                restarts = 0
                if item.status.container_statuses:
                    for cs in item.status.container_statuses:
                        restarts += cs.restart_count or 0
                        if cs.state and cs.state.waiting and cs.state.waiting.reason:
                            phase = cs.state.waiting.reason
                pods.append(
                    PodSummary(
                        name=item.metadata.name,
                        namespace=item.metadata.namespace,
                        phase=phase,
                        restarts=restarts,
                    )
                )
            return True, f"ctx:{context_name} ({active_ns})", pods
        except Exception:
            return self._kubectl_fallback(namespace=namespace)

    def _kubectl_fallback(self, namespace: str | None = None) -> Tuple[bool, str, List[PodSummary]]:
        try:
            ctx_proc = subprocess.run(
                ["kubectl", "config", "current-context"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if ctx_proc.returncode != 0:
                return False, "Offline", []
            ctx_name = ctx_proc.stdout.strip() or "default"

            cmd = [
                "kubectl",
                "get",
                "pods",
                "--no-headers",
                "-o",
                "custom-columns=NAME:.metadata.name,NS:.metadata.namespace,STATUS:.status.phase",
            ]
            if namespace:
                cmd.extend(["-n", namespace])
            pod_proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, check=False)
            if pod_proc.returncode != 0:
                return False, f"Offline ({ctx_name})", []

            pods: List[PodSummary] = []
            for line in pod_proc.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    pods.append(PodSummary(name=parts[0], namespace=parts[1], phase=parts[2]))
            return True, f"ctx:{ctx_name}", pods
        except Exception:
            return False, "Offline", []

