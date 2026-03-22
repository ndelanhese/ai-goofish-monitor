"""
AI analysis service
Encapsulates AI analysis business logic
"""
from typing import Dict, List, Optional
from src.infrastructure.external.ai_client import AIClient


class AIAnalysisService:
    """AI analysis service"""

    def __init__(self, ai_client: AIClient):
        self.ai_client = ai_client

    async def analyze_product(
        self,
        product_data: Dict,
        image_paths: List[str],
        prompt_text: str
    ) -> Optional[Dict]:
        """
        Analyze a product.

        Args:
            product_data: Product data
            image_paths: List of image paths
            prompt_text: Analysis prompt text

        Returns:
            Analysis result
        """
        if not self.ai_client.is_available():
            print("AI client is unavailable, skipping analysis")
            return None

        try:
            result = await self.ai_client.analyze(product_data, image_paths, prompt_text)

            if result and self._validate_result(result):
                return result
            else:
                print("AI analysis result validation failed")
                return None
        except Exception as e:
            print(f"AI analysis service error: {e}")
            return None

    def _validate_result(self, result: Dict) -> bool:
        """Validate the format of an AI analysis result."""
        required_fields = [
            "prompt_version",
            "is_recommended",
            "reason",
            "risk_tags",
            "criteria_analysis"
        ]

        # Check required fields
        for field in required_fields:
            if field not in result:
                print(f"AI response is missing required field: {field}")
                return False

        # Check data types
        if not isinstance(result.get("is_recommended"), bool):
            print("is_recommended field is not a boolean")
            return False

        if not isinstance(result.get("risk_tags"), list):
            print("risk_tags field is not a list")
            return False

        criteria_analysis = result.get("criteria_analysis", {})
        if not isinstance(criteria_analysis, dict) or not criteria_analysis:
            print("criteria_analysis must be a non-empty dict")
            return False

        return True
