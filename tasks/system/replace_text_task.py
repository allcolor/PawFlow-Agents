# Replace Text Task Implementation

"""
Tâche ReplaceText - Remplacer du texte dans le contenu.
"""

import re
from typing import Dict, Any, List
from core import FlowFile, TaskError
from core.base_task import BaseTask


class ReplaceTextTask(BaseTask):
    """
    Tâche pour remplacer du texte dans le contenu du FlowFile.
    
    Supporte le remplacement de texte simple ou regex.
    """
    
    TYPE = "replace_text"
    VERSION = "1.0.0"
    NAME = "Remplacer du texte"
    DESCRIPTION = "Remplacer du texte dans le contenu"
    ICON = "replace"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser la tâche ReplaceText.
        
        Args:
            config: Configuration avec:
                - search_pattern: Motif de recherche (requis)
                - replacement: Texte de remplacement (requis)
                - regex: Utiliser regex (par défaut: false)
                - case_sensitive: Sensible à la casse (par défaut: true)
                - multiline: Multi-lignes (par défaut: false)
        """
        super().__init__(config)
        
        self.search_pattern = self.config.get('search_pattern', '')
        self.replacement = self.config.get('replacement', '')
        self.use_regex = self.config.get('regex', False)
        self.case_sensitive = self.config.get('case_sensitive', True)
        self.multiline = self.config.get('multiline', False)
        
        # Compiler l'expression regex si nécessaire
        self._compile_pattern()
    
    def _compile_pattern(self):
        """Compiler l'expression regex si nécessaire."""
        if self.use_regex:
            flags = 0
            if not self.case_sensitive:
                flags |= re.IGNORECASE
            if self.multiline:
                flags |= re.MULTILINE
            
            try:
                self.pattern = re.compile(self.search_pattern, flags)
            except re.error as e:
                raise TaskError(f"Motif regex invalide: {e}")
        else:
            self.pattern = None
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Exécuter la tâche ReplaceText.
        
        Args:
            flowfile: FlowFile d'entrée
            
        Returns:
            Liste avec le FlowFile modifié
            
        Raises:
            TaskError: Si la tâche échoue
        """
        try:
            # Lire le contenu
            content = self.read_content(flowfile)
            
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            
            # Resolve expression language in replacement (e.g. ${http.path.who})
            from core.expression import resolve_expression
            resolved_replacement = resolve_expression(
                self.replacement,
                attributes=flowfile.get_attributes(),
                parameters=getattr(self, '_parameter_context', None),
            )

            # Effectuer le remplacement
            if self.use_regex and self.pattern:
                new_content = self.pattern.sub(resolved_replacement, content)
            else:
                # Remplacement simple de chaîne
                flags = 0 if self.case_sensitive else re.IGNORECASE
                # Échapper le pattern si ce n'est pas une regex
                escaped_pattern = re.escape(self.search_pattern)
                new_content = re.sub(escaped_pattern, resolved_replacement, content, flags=flags)
            
            # Écrire le contenu modifié
            if isinstance(new_content, str):
                new_content = new_content.encode('utf-8')
            
            self.write_content(flowfile, new_content)
            
            # Mettre à jour l'attribut de taille
            flowfile.set_attribute('fileSize', str(len(new_content)))
            
            return [flowfile]
        
        except UnicodeDecodeError as e:
            raise TaskError(f"Erreur de décodage du contenu: {e}")
        except Exception as e:
            raise TaskError(f"Erreur lors du remplacement: {e}")
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Retourner le schéma des paramètres.
        
        Returns:
            Schema des paramètres pour l'UI
        """
        return {
            'search_pattern': {
                'type': 'string',
                'required': True,
                'description': 'Motif de recherche',
                'placeholder': 'motif à rechercher'
            },
            'replacement': {
                'type': 'string',
                'required': True,
                'description': 'Texte de remplacement',
                'placeholder': 'nouveau texte'
            },
            'regex': {
                'type': 'boolean',
                'required': False,
                'description': 'Utiliser une expression régulière',
                'default': False
            },
            'case_sensitive': {
                'type': 'boolean',
                'required': False,
                'description': 'Sensible à la casse',
                'default': True
            },
            'multiline': {
                'type': 'boolean',
                'required': False,
                'description': 'Mode multi-lignes (pour regex uniquement)',
                'default': False
            }
        }