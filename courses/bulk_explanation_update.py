"""
Bulk Explanation Update for Courses App
Allows updating explanations for CourseQuestion model
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from .models import CourseQuestion


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_update_course_question_explanations(request):
    """
    Bulk update explanations for course questions

    POST /api/courses/questions/bulk-update-explanations/
    (the courses app is mounted at 'api/courses/' in exam_project/urls.py)
    
    Payload:
    {
        "explanations": [
            {"source_question_id": "123", "explanation": "Text here..."},
            {"source_question_id": "456", "explanation": "More text..."}
        ]
    }
    
    Can also use "question_id" field for backward compatibility.
    
    Response:
    {
        "updated": 10,
        "not_found": 2,
        "total_received": 12,
        "errors": []
    }
    """
    # Verify user is staff
    if not request.user.is_staff:
        return Response(
            {'error': 'Only staff users can bulk update explanations'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    explanations_data = request.data.get('explanations', [])
    
    if not isinstance(explanations_data, list):
        return Response(
            {'error': 'explanations must be a list'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    if len(explanations_data) == 0:
        return Response(
            {'error': 'No explanations provided'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    updated_count = 0
    not_found_count = 0
    errors = []
    
    with transaction.atomic():
        for item in explanations_data:
            # Support both "source_question_id" and "question_id" for flexibility
            source_question_id = str(item.get('source_question_id') or item.get('question_id', '')).strip()
            explanation = item.get('explanation', '').strip()
            
            if not source_question_id:
                errors.append('Missing source_question_id or question_id in item')
                continue
            
            try:
                # Find all questions with this source_question_id across all quizzes
                questions = CourseQuestion.objects.filter(source_question_id=source_question_id)
                
                if questions.exists():
                    # Update all matching questions
                    count = questions.update(explanation=explanation)
                    updated_count += count
                else:
                    not_found_count += 1
                    
            except Exception as e:
                errors.append(f'Error updating question {source_question_id}: {str(e)}')
    
    return Response({
        'updated': updated_count,
        'not_found': not_found_count,
        'total_received': len(explanations_data),
        'errors': errors[:10]  # Return first 10 errors only
    }, status=status.HTTP_200_OK)
