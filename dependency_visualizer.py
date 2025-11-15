#!/usr/bin/env python3
"""
Инструмент визуализации графа зависимостей
Этапы 1-5: Конфигурация, сбор данных, построение графа, обратные зависимости и визуализация
"""

import json
import os
import sys
import urllib.request
import urllib.error
import subprocess
from typing import Dict, Any, List, Set
from enum import Enum
from collections import deque


class RepositoryMode(Enum):
    TEST = "test"
    REMOTE = "remote"
    GRAPH_FILE = "graph_file"


class ConfigError(Exception):
    """Исключение для ошибок конфигурации"""
    pass


class DependencyError(Exception):
    """Исключение для ошибок получения зависимостей"""
    pass


class GraphError(Exception):
    """Исключение для ошибок работы с графом"""
    pass


class VisualizationError(Exception):
    """Исключение для ошибок визуализации"""
    pass


class DependencyVisualizer:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self._load_default_config()
        self.dependency_graph = {}
        self.visited_packages = set()
        self.reverse_dependencies = {}

    def _load_default_config(self) -> Dict[str, Any]:
        """Загружает конфигурацию по умолчанию"""
        return {
            "package_name": "",
            "repository_url": "",
            "repository_mode": RepositoryMode.TEST.value,
            "show_reverse_deps": False,
            "generate_graphviz": False,
            "generate_image": False
        }

    def _validate_config(self, config: Dict[str, Any]) -> None:
        """Валидация конфигурационных параметров"""

        if not config.get("package_name"):
            raise ConfigError("Имя пакета не может быть пустым")

        package_name = config["package_name"]
        if not isinstance(package_name, str):
            raise ConfigError("Имя пакета должно быть строкой")
        if len(package_name.strip()) == 0:
            raise ConfigError("Имя пакета не может состоять только из пробельных символов")

        repository_url = config.get("repository_url", "")
        if not repository_url:
            raise ConfigError("URL репозитория или путь не может быть пустым")

        if not isinstance(repository_url, str):
            raise ConfigError("URL репозитория должен быть строкой")

        repository_mode = config.get("repository_mode", "")
        if not repository_mode:
            raise ConfigError("Режим репозитория не может быть пустым")

        valid_modes = [mode.value for mode in RepositoryMode]
        if repository_mode not in valid_modes:
            raise ConfigError(f"Недопустимый режим репозитория. Допустимые значения: {', '.join(valid_modes)}")

        if repository_mode == RepositoryMode.TEST.value:
            if not os.path.exists(repository_url):
                raise ConfigError(f"Тестовый репозиторий не найден по пути: {repository_url}")
        elif repository_mode == RepositoryMode.REMOTE.value:
            if not (repository_url.startswith('http://') or repository_url.startswith('https://')):
                raise ConfigError("URL удаленного репозитория должен начинаться с http:// или https://")
        elif repository_mode == RepositoryMode.GRAPH_FILE.value:
            if not os.path.exists(repository_url):
                raise ConfigError(f"Файл графа не найден по пути: {repository_url}")

    def load_config(self) -> Dict[str, Any]:
        """Загружает конфигурацию из файла"""
        try:
            if not os.path.exists(self.config_path):
                raise ConfigError(f"Конфигурационный файл не найден: {self.config_path}")

            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            self.config.update(config_data)
            self._validate_config(self.config)

            return self.config

        except json.JSONDecodeError as e:
            raise ConfigError(f"Ошибка парсинга JSON в файле конфигурации: {e}")
        except UnicodeDecodeError as e:
            raise ConfigError(f"Ошибка декодирования файла конфигурации: {e}")
        except IOError as e:
            raise ConfigError(f"Ошибка чтения файла конфигурации: {e}")

    def display_config(self) -> None:
        """Выводит конфигурацию в формате ключ-значение"""
        print("Текущая конфигурация:")
        print("-" * 40)
        for key, value in self.config.items():
            print(f"{key}: {value}")
        print("-" * 40)

    def _fetch_package_info_from_npm(self, package_name: str) -> Dict[str, Any]:
        """Получает информацию о пакете из npm registry"""
        url = f"https://registry.npmjs.org/{package_name}"

        try:
            headers = {
                'User-Agent': 'DependencyVisualizer/1.0',
                'Accept': 'application/json'
            }

            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return data
                else:
                    raise DependencyError(f"Не удалось получить информацию о пакете. HTTP статус: {response.status}")

        except urllib.error.URLError as e:
            raise DependencyError(f"Ошибка сети при получении информации о пакете: {e}")
        except json.JSONDecodeError as e:
            raise DependencyError(f"Ошибка парсинга ответа от npm registry: {e}")
        except Exception as e:
            raise DependencyError(f"Неожиданная ошибка при получении информации о пакете: {e}")

    def _find_version_with_dependencies(self, package_info: Dict[str, Any]) -> str:
        """Находит версию пакета, которая имеет зависимости"""
        versions = package_info.get("versions", {})

        # Сначала проверяем последнюю версию
        latest_version = package_info.get("dist-tags", {}).get("latest")
        if latest_version and latest_version in versions:
            version_data = versions[latest_version]
            if version_data.get("dependencies"):
                return latest_version

        # Если в последней версии нет зависимостей, ищем в более старых версиях
        sorted_versions = sorted(versions.keys(), reverse=True)

        for version in sorted_versions:
            version_data = versions[version]
            if version_data.get("dependencies"):
                print(f"Найдены зависимости в версии {version} (не самой новой)")
                return version

        # Если вообще не нашли зависимостей
        return latest_version if latest_version and latest_version in versions else sorted_versions[
            0] if sorted_versions else None

    def _get_dependencies_from_test_repo(self, package_name: str, repo_path: str) -> Dict[str, str]:
        """Получает зависимости из тестового репозитория"""
        package_json_path = os.path.join(repo_path, package_name, "package.json")

        if not os.path.exists(package_json_path):
            raise DependencyError(f"Файл package.json не найден для пакета {package_name} в тестовом репозитории")

        try:
            with open(package_json_path, 'r', encoding='utf-8') as f:
                package_data = json.load(f)

            dependencies = package_data.get("dependencies", {})
            return dependencies

        except json.JSONDecodeError as e:
            raise DependencyError(f"Ошибка парсинга package.json: {e}")
        except IOError as e:
            raise DependencyError(f"Ошибка чтения package.json: {e}")

    def _load_graph_from_file(self, file_path: str) -> Dict[str, List[str]]:
        """Загружает граф зависимостей из файла"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                graph_data = json.load(f)

            # Валидируем структуру графа
            if not isinstance(graph_data, dict):
                raise GraphError("Файл графа должен содержать JSON объект")

            for package, dependencies in graph_data.items():
                if not isinstance(package, str):
                    raise GraphError("Ключи в графе должны быть строками")
                if not isinstance(dependencies, list):
                    raise GraphError(f"Зависимости для пакета {package} должны быть списком")
                for dep in dependencies:
                    if not isinstance(dep, str):
                        raise GraphError(f"Зависимости должны быть строками в пакете {package}")

            return graph_data

        except json.JSONDecodeError as e:
            raise GraphError(f"Ошибка парсинга JSON в файле графа: {e}")
        except IOError as e:
            raise GraphError(f"Ошибка чтения файла графа: {e}")

    def get_direct_dependencies(self, package_name: str = None) -> Dict[str, str]:
        """Получает прямые зависимости пакета"""
        if package_name is None:
            package_name = self.config["package_name"]

        repository_mode = self.config["repository_mode"]
        repository_url = self.config["repository_url"]

        try:
            if repository_mode == RepositoryMode.REMOTE.value:
                # Используем npm registry для удаленного режима
                package_info = self._fetch_package_info_from_npm(package_name)

                # Находим версию с зависимостями
                version_to_use = self._find_version_with_dependencies(package_info)

                if not version_to_use:
                    return {}

                # Получаем зависимости для выбранной версии
                version_info = package_info["versions"][version_to_use]
                dependencies = version_info.get("dependencies", {})

                return dependencies

            elif repository_mode == RepositoryMode.TEST.value:
                # Используем локальный тестовый репозиторий
                return self._get_dependencies_from_test_repo(package_name, repository_url)

            elif repository_mode == RepositoryMode.GRAPH_FILE.value:
                # Используем файл графа
                graph = self._load_graph_from_file(repository_url)
                if package_name in graph:
                    return {dep: "*" for dep in graph[package_name]}
                else:
                    return {}

        except DependencyError:
            raise
        except Exception as e:
            raise DependencyError(f"Неожиданная ошибка при получении зависимостей: {e}")

    def build_dependency_graph_bfs(self) -> Dict[str, Dict[str, Any]]:
        """Строит полный граф зависимостей с использованием BFS"""
        root_package = self.config["package_name"]
        repository_mode = self.config["repository_mode"]

        print(f"\nПостроение графа зависимостей для пакета: {root_package}")
        print(f"Режим: {repository_mode}")

        # Инициализация графа
        self.dependency_graph = {}
        self.visited_packages = set()
        self.reverse_dependencies = {}
        cycles_detected = []

        # Если режим graph_file, загружаем весь граф сразу
        if repository_mode == RepositoryMode.GRAPH_FILE.value:
            file_graph = self._load_graph_from_file(self.config["repository_url"])
            self._build_graph_from_file_bfs(root_package, file_graph, cycles_detected)
        else:
            # Для других режимов используем BFS с рекурсивным получением зависимостей
            self._build_graph_bfs_recursive(root_package, [], cycles_detected)

        # Строим граф обратных зависимостей
        self._build_reverse_dependencies()

        # Выводим информацию о циклических зависимостях
        if cycles_detected:
            print(f"\n⚠️  Обнаружены циклические зависимости ({len(cycles_detected)}):")
            for cycle in cycles_detected:
                # Убираем дубликаты и формируем корректное представление цикла
                clean_cycle = []
                for node in cycle:
                    if node not in clean_cycle:
                        clean_cycle.append(node)
                cycle_str = " -> ".join(clean_cycle)
                print(f"   🔁 {cycle_str} -> {clean_cycle[0]}")

        return self.dependency_graph

    def _build_graph_bfs_recursive(self, package: str, path: List[str], cycles_detected: List[List[str]]) -> None:
        """Рекурсивная часть BFS для построения графа"""
        if package in self.visited_packages:
            # Проверяем циклическую зависимость
            if package in path:
                cycle_start = path.index(package)
                cycle = path[cycle_start:]  # Берем только часть пути, образующую цикл
                # Нормализуем цикл (убираем дубликаты)
                normalized_cycle = []
                for node in cycle:
                    if node not in normalized_cycle:
                        normalized_cycle.append(node)
                if normalized_cycle not in cycles_detected:
                    cycles_detected.append(normalized_cycle)
            return

        self.visited_packages.add(package)
        current_path = path + [package]

        try:
            # Получаем прямые зависимости
            dependencies = self.get_direct_dependencies(package)
            self.dependency_graph[package] = {
                'dependencies': dependencies,
                'level': len(current_path) - 1
            }

            # Рекурсивно обрабатываем зависимости
            for dep_name in dependencies.keys():
                self._build_graph_bfs_recursive(dep_name, current_path, cycles_detected)

        except DependencyError as e:
            print(f"⚠️  Ошибка при получении зависимостей для {package}: {e}")
            self.dependency_graph[package] = {
                'dependencies': {},
                'level': len(current_path) - 1,
                'error': str(e)
            }

    def _build_graph_from_file_bfs(self, root_package: str, file_graph: Dict[str, List[str]],
                                   cycles_detected: List[List[str]]) -> None:
        """BFS для построения графа из файла"""
        queue = deque([(root_package, 0, [])])  # (package, level, path)

        while queue:
            package, level, path = queue.popleft()

            if package in self.visited_packages:
                # Проверяем циклическую зависимость
                if package in path:
                    cycle_start = path.index(package)
                    cycle = path[cycle_start:]  # Берем только часть пути, образующую цикл
                    # Нормализуем цикл (убираем дубликаты)
                    normalized_cycle = []
                    for node in cycle:
                        if node not in normalized_cycle:
                            normalized_cycle.append(node)
                    if normalized_cycle not in cycles_detected:
                        cycles_detected.append(normalized_cycle)
                continue

            self.visited_packages.add(package)
            current_path = path + [package]

            # Добавляем пакет в граф
            if package in file_graph:
                dependencies = {dep: "*" for dep in file_graph[package]}
                self.dependency_graph[package] = {
                    'dependencies': dependencies,
                    'level': level
                }

                # Добавляем зависимости в очередь
                for dep_name in file_graph[package]:
                    queue.append((dep_name, level + 1, current_path))
            else:
                self.dependency_graph[package] = {
                    'dependencies': {},
                    'level': level,
                    'error': f"Пакет {package} не найден в графе"
                }

    def _build_reverse_dependencies(self) -> None:
        """Строит граф обратных зависимостей"""
        self.reverse_dependencies = {}

        for package, info in self.dependency_graph.items():
            for dep in info['dependencies']:
                if dep not in self.reverse_dependencies:
                    self.reverse_dependencies[dep] = []
                if package not in self.reverse_dependencies[dep]:
                    self.reverse_dependencies[dep].append(package)

    def get_reverse_dependencies(self, package_name: str = None) -> List[str]:
        """Получает обратные зависимости для пакета"""
        if package_name is None:
            package_name = self.config["package_name"]

        return self.reverse_dependencies.get(package_name, [])

    def display_reverse_dependencies(self, package_name: str = None) -> None:
        """Выводит обратные зависимости для пакета"""
        if package_name is None:
            package_name = self.config["package_name"]

        reverse_deps = self.get_reverse_dependencies(package_name)

        print(f"\n🔄 Обратные зависимости для пакета '{package_name}':")
        print("-" * 50)

        if not reverse_deps:
            print("Обратные зависимости не найдены.")
            print("(Ни один пакет не зависит от данного пакета)")
            return

        for dep in sorted(reverse_deps):
            print(f"• {dep}")

        print(f"\nВсего пакетов, зависящих от '{package_name}': {len(reverse_deps)}")

    def _check_graphviz_installed(self) -> bool:
        """Проверяет, установлен ли Graphviz"""
        try:
            # Пробуем разные возможные пути к dot.exe в Windows
            possible_paths = [
                'dot',
                'dot.exe',
                r'C:\Program Files\Graphviz\bin\dot.exe',
                r'C:\Program Files (x86)\Graphviz\bin\dot.exe'
            ]

            for dot_path in possible_paths:
                try:
                    result = subprocess.run([dot_path, '-V'], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return True
                except (FileNotFoundError, subprocess.SubprocessError):
                    continue

            return False
        except Exception:
            return False

    def _generate_image_from_dot(self, dot_filename: str, output_format: str = "png") -> str:
        """Генерирует изображение из DOT файла"""
        if not self._check_graphviz_installed():
            raise VisualizationError(
                "Graphviz не установлен или не найден в PATH.\n"
                "Установите Graphviz одним из способов:\n"
                "1. Скачайте с https://graphviz.org/download/ и установите\n"
                "2. Через Chocolatey: choco install graphviz\n"
                "3. Добавьте путь к Graphviz в переменную PATH\n"
                "   (обычно C:\\Program Files\\Graphviz\\bin\\)"
            )

        output_filename = dot_filename.replace('.dot', f'.{output_format}')

        try:
            # Используем полный путь к dot.exe для Windows
            dot_command = 'dot.exe'

            result = subprocess.run(
                [dot_command, f'-T{output_format}', dot_filename, '-o', output_filename],
                capture_output=True,
                text=True,
                timeout=30,
                shell=True  # Добавляем shell=True для Windows
            )

            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else "Неизвестная ошибка"
                raise VisualizationError(f"Ошибка Graphviz: {error_msg}")

            # Проверяем, что файл создан
            if not os.path.exists(output_filename):
                raise VisualizationError(f"Файл {output_filename} не был создан")

            return output_filename

        except subprocess.TimeoutExpired:
            raise VisualizationError("Превышено время генерации изображения")
        except Exception as e:
            raise VisualizationError(f"Ошибка при генерации изображения: {e}")

    def generate_graphviz(self) -> str:
        """Генерирует описание графа на языке Graphviz DOT"""
        if not self.dependency_graph:
            return ""

        root_package = self.config["package_name"]

        dot_lines = [
            "digraph DependencyGraph {",
            "    rankdir=TB;",
            "    node [shape=box, style=filled, fillcolor=lightblue, fontname=Arial];",
            "    edge [color=darkgreen, fontname=Arial];",
            "    graph [fontname=Arial];",
            "",
            f'    label="Граф зависимостей для {root_package}";',
            f'    labelloc=t;',
            f'    fontsize=16;',
            ""
        ]

        # Добавляем узлы
        for package, info in self.dependency_graph.items():
            # Разные стили для разных типов узлов
            if package == root_package:
                node_style = 'shape=ellipse, style=filled, fillcolor=orange, fontsize=12'
            elif 'error' in info:
                node_style = 'style=filled, fillcolor=lightcoral, fontsize=10'
            elif not info['dependencies']:
                node_style = 'style=filled, fillcolor=lightgreen, fontsize=10'
            else:
                node_style = 'style=filled, fillcolor=lightblue, fontsize=10'

            dot_lines.append(f'    "{package}" [{node_style}];')

        dot_lines.append("")

        # Добавляем рёбра (зависимости)
        dot_lines.append("    // Зависимости между пакетами")
        edges_added = set()

        for package, info in self.dependency_graph.items():
            for dep in info['dependencies']:
                edge = f'"{package}" -> "{dep}"'
                if edge not in edges_added:
                    dot_lines.append(f"    {edge};")
                    edges_added.add(edge)

        dot_lines.append("}")

        return "\n".join(dot_lines)

    def generate_simple_graphviz(self) -> str:
        """Генерирует упрощенную версию DOT для отладки"""
        if not self.dependency_graph:
            return ""

        root_package = self.config["package_name"]

        dot_lines = [
            "digraph G {",
            "    rankdir=LR;",
            "    node [shape=box];",
            f'    label="Dependencies for {root_package}";',
            ""
        ]

        # Просто добавляем все узлы и рёбра
        for package, info in self.dependency_graph.items():
            for dep in info['dependencies']:
                dot_lines.append(f'    "{package}" -> "{dep}";')

        dot_lines.append("}")

        return "\n".join(dot_lines)

    def _find_all_cycles(self) -> List[List[str]]:
        """Находит все циклы в графе"""
        cycles = []
        visited = set()

        def dfs(node, path):
            if node in path:
                cycle_start = path.index(node)
                cycle = path[cycle_start:]
                # Нормализуем цикл
                normalized_cycle = []
                for n in cycle:
                    if n not in normalized_cycle:
                        normalized_cycle.append(n)
                if normalized_cycle not in cycles:
                    cycles.append(normalized_cycle)
                return

            if node in visited:
                return

            visited.add(node)
            path.append(node)

            if node in self.dependency_graph:
                for neighbor in self.dependency_graph[node]['dependencies']:
                    dfs(neighbor, path.copy())

            path.pop()

        for node in self.dependency_graph:
            if node not in visited:
                dfs(node, [])

        return cycles

    def display_direct_dependencies(self) -> None:
        """Выводит только прямые зависимости (для этапа 2)"""
        dependencies = self.get_direct_dependencies()

        if not dependencies:
            print("Прямые зависимости не найдены.")
            return

        print(f"\nПрямые зависимости пакета '{self.config['package_name']}':")
        print("-" * 50)
        for dep_name, dep_version in sorted(dependencies.items()):
            print(f"• {dep_name}: {dep_version}")
        print(f"Всего зависимостей: {len(dependencies)}")

    def display_dependency_graph(self) -> None:
        """Выводит полный граф зависимостей"""
        if not self.dependency_graph:
            print("Граф зависимостей не построен.")
            return

        root_package = self.config["package_name"]

        print(f"\nПолный граф зависимостей для пакета '{root_package}':")
        print("=" * 60)

        # Группируем пакеты по уровням
        levels = {}
        for package, info in self.dependency_graph.items():
            level = info['level']
            if level not in levels:
                levels[level] = []
            levels[level].append(package)

        # Выводим пакеты по уровням
        for level in sorted(levels.keys()):
            packages = sorted(levels[level])
            indent = "  " * level
            print(f"{indent}📦 Уровень {level}: {', '.join(packages)}")

        # Статистика
        total_packages = len(self.dependency_graph)
        total_dependencies = sum(len(info['dependencies']) for info in self.dependency_graph.values())

        print(f"\n📊 Статистика графа:")
        print(f"   • Всего пакетов: {total_packages}")
        print(f"   • Всего зависимостей: {total_dependencies}")
        print(f"   • Максимальная глубина: {max(levels.keys()) if levels else 0}")

    def display_detailed_dependencies(self) -> None:
        """Выводит детальную информацию о зависимостях"""
        if not self.dependency_graph:
            print("Граф зависимостей не построен.")
            return

        root_package = self.config["package_name"]

        print(f"\nДетальная информация о зависимостях '{root_package}':")
        print("=" * 60)

        for package, info in sorted(self.dependency_graph.items()):
            level = info['level']
            dependencies = info['dependencies']
            indent = "  " * level

            if dependencies:
                deps_str = ", ".join(f"{dep}" for dep in sorted(dependencies.keys()))
                print(f"{indent}📦 {package} (уровень {level}) → {deps_str}")
            else:
                if 'error' in info:
                    print(f"{indent}❌ {package} (уровень {level}) - {info['error']}")
                else:
                    print(f"{indent}✅ {package} (уровень {level}) - нет зависимостей")


def create_sample_config() -> None:
    """Создает пример конфигурационного файла"""
    sample_config = {
        "package_name": "A",
        "repository_url": "test_graphs/cyclic_graph.json",
        "repository_mode": "graph_file",
        "show_reverse_deps": False,
        "generate_graphviz": False,
        "generate_image": False
    }

    with open("config.json", 'w', encoding='utf-8') as f:
        json.dump(sample_config, f, indent=2, ensure_ascii=False)

    print("Создан пример конфигурационного файла 'config.json'")


def create_complete_test_repository():
    """Создает полноценный тестовый репозиторий с взаимосвязанными пакетами"""
    test_dir = "test_repository"
    os.makedirs(test_dir, exist_ok=True)

    packages = {
        "react": {
            "name": "react",
            "version": "18.2.0",
            "dependencies": {
                "loose-envify": "^1.1.0",
                "object-assign": "^4.1.1"
            }
        },
        "express": {
            "name": "express",
            "version": "4.18.2",
            "dependencies": {
                "accepts": "~1.3.8",
                "body-parser": "1.20.1"
            }
        },
        "webapp": {
            "name": "webapp",
            "version": "1.0.0",
            "dependencies": {
                "react": "^18.2.0",
                "express": "^4.18.2",
                "axios": "^1.0.0"
            }
        },
        "axios": {
            "name": "axios",
            "version": "1.0.0",
            "dependencies": {
                "follow-redirects": "^1.15.0"
            }
        }
    }

    for package_name, package_data in packages.items():
        package_dir = os.path.join(test_dir, package_name)
        os.makedirs(package_dir, exist_ok=True)

        package_json_path = os.path.join(package_dir, "package.json")
        with open(package_json_path, 'w', encoding='utf-8') as f:
            json.dump(package_data, f, indent=2, ensure_ascii=False)

    print(f"Создан тестовый репозиторий в '{test_dir}'")


def create_graph_files():
    """Создает тестовые файлы графов для демонстрации"""
    graphs_dir = "test_graphs"
    os.makedirs(graphs_dir, exist_ok=True)

    # Простой граф без циклов
    simple_graph = {
        "A": ["B", "C"],
        "B": ["D", "E"],
        "C": ["F"],
        "D": [],
        "E": ["F"],
        "F": []
    }

    # Граф с циклическими зависимостями
    cyclic_graph = {
        "A": ["B"],
        "B": ["C"],
        "C": ["A"],  # Цикл A -> B -> C -> A
        "D": ["E", "F"],
        "E": ["D"],  # Цикл D -> E -> D
        "F": []
    }

    # Сложный граф с несколькими циклами
    complex_graph = {
        "START": ["A", "B"],
        "A": ["C", "D"],
        "B": ["D", "E"],
        "C": ["F"],
        "D": ["G", "H"],
        "E": ["H", "I"],
        "F": ["J"],
        "G": ["K"],
        "H": ["L", "M"],
        "I": ["M"],
        "J": ["N"],
        "K": ["L"],
        "L": ["K"],  # Цикл K -> L -> K
        "M": ["N"],
        "N": ["O"],
        "O": ["P"],
        "P": ["M"]  # Цикл M -> N -> O -> P -> M
    }

    # Сохраняем графы в файлы
    with open(os.path.join(graphs_dir, "simple_graph.json"), 'w', encoding='utf-8') as f:
        json.dump(simple_graph, f, indent=2)

    with open(os.path.join(graphs_dir, "cyclic_graph.json"), 'w', encoding='utf-8') as f:
        json.dump(cyclic_graph, f, indent=2)

    with open(os.path.join(graphs_dir, "complex_graph.json"), 'w', encoding='utf-8') as f:
        json.dump(complex_graph, f, indent=2)

    print(f"Созданы тестовые графы в '{graphs_dir}'")
    print("Доступные графы: simple_graph.json, cyclic_graph.json, complex_graph.json")


def create_installation_guide():
    """Создает руководство по установке Graphviz"""
    guide = """
📋 РУКОВОДСТВО ПО УСТАНОВКЕ GRAPHVIZ ДЛЯ ВИЗУАЛИЗАЦИИ

1. 📥 СКАЧАТЬ GRAPHVIZ:
   - Перейдите на: https://graphviz.org/download/
   - Выберите "Windows" → "graphviz-*-win32.exe"
   - Скачайте и запустите установщик

2. 🛠️ УСТАНОВКА:
   - Запустите установщик
   - Выберите "Install for all users" 
   - Оставьте путь по умолчанию (C:\\Program Files\\Graphviz\\)
   - Отметьте "Add Graphviz to the system PATH for all users"

3. 🔄 ПЕРЕЗАПУСТИТЕ КОНСОЛЬ:
   - Закройте и откройте заново PowerShell/Command Prompt
   - Проверьте установку: dot -V

4. 🎨 АЛЬТЕРНАТИВНЫЕ ВАРИАНТЫ:
   - Через Chocolatey: choco install graphviz
   - Используйте онлайн визуализатор: https://edotor.net/

5. 🚀 БЫСТРАЯ ПРОВЕРКА:
   После установки запустите:
   python dependency_visualizer.py (с generate_image: true)
"""
    print(guide)


def main():
    """Основная функция приложения"""
    print("Инструмент визуализации графа зависимостей - Этапы 1-5")
    print("=" * 60)

    # Создаем пример конфигурации и тестовые данные, если нужно
    if not os.path.exists("config.json"):
        print("Конфигурационный файл не найден.")
        create_sample_config()
        create_complete_test_repository()
        create_graph_files()

        print("\nПримеры конфигураций для тестирования:")
        print('1. Обратные зависимости:')
        print(
            '   {"package_name": "A", "repository_url": "test_graphs/simple_graph.json", "repository_mode": "graph_file", "show_reverse_deps": true}')

        print('\n2. Graphviz визуализация:')
        print(
            '   {"package_name": "A", "repository_url": "test_graphs/cyclic_graph.json", "repository_mode": "graph_file", "generate_graphviz": true, "generate_image": true}')

        print('\n3. Полный анализ:')
        print(
            '   {"package_name": "webapp", "repository_url": "test_repository", "repository_mode": "test", "show_reverse_deps": true, "generate_graphviz": true, "generate_image": true}')

        print("\nОтредактируйте config.json и запустите приложение снова.")
        return

    # Загружаем конфигурацию
    visualizer = DependencyVisualizer()

    try:
        config = visualizer.load_config()
        visualizer.display_config()

        # Строим полный граф зависимостей
        dependency_graph = visualizer.build_dependency_graph_bfs()

        # Выводим основные результаты
        visualizer.display_detailed_dependencies()
        visualizer.display_dependency_graph()

        # Этап 4: Обратные зависимости
        if config.get("show_reverse_deps", False):
            visualizer.display_reverse_dependencies()

        # Этап 5: Graphviz визуализация
        if config.get("generate_graphviz", False):
            # Сначала генерируем упрощенную версию для тестирования
            simple_dot = visualizer.generate_simple_graphviz()
            dot_filename = f"dependency_graph_{config['package_name']}.dot"

            with open(dot_filename, 'w', encoding='utf-8') as f:
                f.write(simple_dot)

            print(f"\n🎨 Graphviz DOT представление графа:")
            print("=" * 60)
            print(simple_dot)
            print(f"\n💾 Graphviz код сохранен в файл: {dot_filename}")

            # Генерация изображения
            if config.get("generate_image", False):
                try:
                    image_filename = visualizer._generate_image_from_dot(dot_filename)
                    print(f"🖼️  Изображение графа сохранено: {image_filename}")

                    # Показываем созданные файлы
                    print("\n📁 Созданные файлы:")
                    for file in os.listdir('.'):
                        if file.startswith('dependency_graph_'):
                            size = os.path.getsize(file)
                            print(f"   • {file} ({size} bytes)")

                    print("\n📋 Сравнение с npm:")
                    print("   ✅ Наш инструмент показывает полный граф с транзитивными зависимостями")
                    print("   ✅ Выделяет циклические зависимости")
                    print("   ✅ Показывает обратные зависимости")
                    print("   ❌ npm ls показывает только зависимости текущего проекта")
                    print("   ❌ npm не показывает циклические зависимости явно")

                except VisualizationError as e:
                    print(f"❌ Ошибка визуализации: {e}")
                    create_installation_guide()

    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}")
        sys.exit(1)
    except DependencyError as e:
        print(f"Ошибка получения зависимостей: {e}")
        sys.exit(1)
    except GraphError as e:
        print(f"Ошибка работы с графом: {e}")
        sys.exit(1)
    except VisualizationError as e:
        print(f"Ошибка визуализации: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Неожиданная ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()